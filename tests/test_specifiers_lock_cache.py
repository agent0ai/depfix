from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from nturl2path import url2pathname as windows_url2pathname
from pathlib import Path

import pytest
from conftest import file_spec, sha256
from jsonschema import Draft202012Validator

from depfix import _file_urls
from depfix import cache as cache_module
from depfix.cache import Cache
from depfix.errors import CacheError, IntegrityError
from depfix.manifest import computed_graph_id, dumps, load, write
from depfix.models import Alias, Artifact, Environment, LockedGraph, Node
from depfix.specifiers import parse_specifier
from depfix.wheel import extract_wheel


def test_unified_source_grammar(tmp_path: Path) -> None:
    source = tmp_path / "mod.py"
    source.write_text("answer = 42\n", encoding="utf-8")
    parsed = parse_specifier(file_spec(source, kind="py"))
    assert parsed.kind == "py"
    assert parsed.sha256 == sha256(source)
    pypi = parse_specifier("pypi:Demo_Project[b,a]==1.2")
    assert pypi.distribution == "demo-project"
    assert pypi.extras == ("a", "b")
    local_url = parse_specifier(source.resolve().as_uri())
    assert local_url.kind == "py"
    assert local_url.path == source.resolve()
    remote = parse_specifier("py:https://example.test/mod.py#sha256=" + "0" * 64)
    assert remote.kind == "py"
    assert remote.sha256 == "0" * 64


def test_file_url_conversion_preserves_windows_drive_letters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_file_urls, "url2pathname", windows_url2pathname)

    path = _file_urls.file_url_to_path("file:///C:/Users/example/My%20Package/demo.py")

    assert str(path) == r"C:\Users\example\My Package\demo.py"


def test_lockfile_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    digest = "a" * 64
    artifact = Artifact(f"sha256:{digest}", "demo", "1.0", "file:///demo.whl", "demo-1.0-py3-none-any.whl", 3, digest)
    node = Node("node_" + "1" * 24, artifact.id, "demo", "1.0", provided_modules=("demo",))
    graph = LockedGraph(
        1,
        "",
        "depfix 0.1.0",
        Environment("cpython", "3.11", "abi", "linux", "arm64"),
        (artifact,),
        (node,),
        (Alias("demo", node.id, "demo", "pypi:demo==1.0", allow_unsafe=True),),
        {"strict": True, "allow-unsafe": True},
    )
    graph = LockedGraph(
        graph.format_version,
        computed_graph_id(graph),
        graph.created_by,
        graph.environment,
        graph.artifacts,
        graph.nodes,
        graph.aliases,
        graph.policy,
    )
    assert dumps(graph) == dumps(graph)
    path = tmp_path / ".depfix" / "imports.lock"
    write(graph, path)
    loaded = load(path)
    assert loaded == graph
    assert loaded.aliases[0].allow_unsafe is True
    assert loaded.policy["allow-unsafe"] is True
    schema = tomllib.loads(path.read_text(encoding="utf-8"))
    definition = json.loads(
        (Path(__file__).parents[1] / "schemas" / "depfix-manifest-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(definition).validate(schema)


def test_hash_mismatch_never_populates_cache(tmp_path: Path) -> None:
    source = tmp_path / "payload.py"
    source.write_bytes(b"safe = True\n")
    cache = Cache(tmp_path / "cache")
    with pytest.raises(IntegrityError):
        cache.fetch_url(source.as_uri(), "0" * 64)
    assert cache.list_blobs() == []


def test_truncated_download_resumes_before_cache_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"resumable artifact bytes"
    split_at = 10
    digest = hashlib.sha256(payload).hexdigest()
    requests: list[str | urllib.request.Request] = []

    class Response(io.BytesIO):
        def __init__(self, body: bytes, headers: dict[str, str]) -> None:
            super().__init__(body)
            self.headers = headers

        def geturl(self) -> str:
            return "https://files.example.test/artifact.whl"

    def open_url(request: str | urllib.request.Request, **_kwargs: object) -> Response:
        requests.append(request)
        if len(requests) == 1:
            return Response(payload[:split_at], {"Content-Length": str(len(payload))})
        assert isinstance(request, urllib.request.Request)
        assert request.get_header("Range") == f"bytes={split_at}-"
        return Response(
            payload[split_at:],
            {
                "Content-Length": str(len(payload) - split_at),
                "Content-Range": f"bytes {split_at}-{len(payload) - 1}/{len(payload)}",
            },
        )

    monkeypatch.setattr(cache_module, "_open_url", open_url)
    cache = Cache(tmp_path / "cache")

    result = cache.fetch_url(
        "https://files.example.test/artifact.whl",
        digest,
        expected_size=len(payload),
    )

    assert result.read_bytes() == payload
    assert len(requests) == 2


def test_concurrent_cache_population_is_atomic(tmp_path: Path) -> None:
    source = tmp_path / "large.whl"
    source.write_bytes(b"wheel bytes" * 100_000)
    digest = sha256(source)
    cache_root = tmp_path / "cache"
    code = (
        "from pathlib import Path; from depfix.cache import Cache; "
        f"Cache(Path({str(cache_root)!r})).fetch_url({source.as_uri()!r}, {digest!r})"
    )
    processes = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(4)]
    assert [process.wait() for process in processes] == [0, 0, 0, 0]
    assert Cache(cache_root).verify_blob(digest).read_bytes() == source.read_bytes()


def test_windows_cache_lock_retries_transient_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = Cache(tmp_path / "cache", timeout=1.0)
    digest = "a" * 64
    lock = cache.root / "locks" / f"{digest}.lock"
    real_mkdir = Path.mkdir
    attempts = 0

    def racing_mkdir(path: Path, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        nonlocal attempts
        if path == lock and attempts < 2:
            attempts += 1
            raise PermissionError(13, "Windows lock directory is being removed", path)
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(cache_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(Path, "mkdir", racing_mkdir)

    with cache._artifact_lock(digest):
        assert lock.is_dir()

    assert attempts == 2
    assert not lock.exists()


def test_non_windows_cache_lock_preserves_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = Cache(tmp_path / "cache", timeout=1.0)
    digest = "b" * 64
    lock = cache.root / "locks" / f"{digest}.lock"
    real_mkdir = Path.mkdir

    def denied_mkdir(path: Path, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        if path == lock:
            raise PermissionError(13, "Cache root is not writable", path)
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(cache_module, "_IS_WINDOWS", False)
    monkeypatch.setattr(Path, "mkdir", denied_mkdir)

    with pytest.raises(PermissionError, match="Cache root is not writable"):
        with cache._artifact_lock(digest):
            raise AssertionError("unreachable")


def test_malicious_wheel_path_is_rejected(tmp_path: Path) -> None:
    wheel = tmp_path / "bad-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("../escape.py", "bad = True")
    with pytest.raises(CacheError, match="Unsafe path"):
        extract_wheel(wheel, tmp_path / "out")
    assert not (tmp_path / "escape.py").exists()


def _rewrite_record(
    wheel: Path,
    transform,
    *,
    added_members: dict[str, bytes] | None = None,
) -> None:  # type: ignore[no-untyped-def]
    with zipfile.ZipFile(wheel) as archive:
        members = {info.filename: archive.read(info) for info in archive.infolist()}
    record_name = next(name for name in members if name.endswith(".dist-info/RECORD"))
    rows = list(csv.reader(members[record_name].decode("utf-8").splitlines()))
    transformed = transform(rows)
    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerows(transformed)
    members[record_name] = stream.getvalue().encode()
    members.update(added_members or {})
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


@pytest.mark.parametrize(
    ("archive_path", "installed_path"),
    [
        ("record_coverage.py", "purelib/record_coverage.py"),
        ("record_coverage/native.so", "purelib/record_coverage/native.so"),
        ("record_coverage-1.0.data/purelib/extra.py", "purelib/extra.py"),
        ("record_coverage-1.0.data/platlib/extra.so", "platlib/extra.so"),
        ("record_coverage-1.0.data/data/resource.bin", "data/resource.bin"),
    ],
)
def test_wheel_safe_unlisted_members_use_depfix_payload_manifest(
    tmp_path: Path,
    wheel_factory,
    archive_path: str,
    installed_path: str,
) -> None:
    wheel = wheel_factory("record-coverage", "1.0", {archive_path: b"payload"})
    _rewrite_record(wheel, lambda rows: [row for row in rows if row[0] != archive_path])

    target = tmp_path / "out"
    extract_wheel(wheel, target)

    assert (target / installed_path).read_bytes() == b"payload"
    marker = json.loads((target / ".complete").read_text(encoding="utf-8"))
    assert marker["artifact_sha256"] == sha256(wheel)
    assert marker["file_hashes"][installed_path] == hashlib.sha256(b"payload").hexdigest()


@pytest.mark.skipif(os.name == "nt", reason="POSIX target modes do not apply on Windows")
def test_wheel_materializes_every_payload_file_readable_executable_and_immutable(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "uniform-modes",
        "1.0",
        {
            "uniform_modes.py": "VALUE = 1\n",
            "uniform_modes/native.so": b"native",
            "uniform_modes/driver/node": b"#!/bin/sh\nexit 0\n",
        },
    )
    target = tmp_path / "out"

    extract_wheel(wheel, target)

    payloads = [path for path in target.rglob("*") if path.is_file() and path.name != ".complete"]
    directories = [target, *(path for path in target.rglob("*") if path.is_dir())]
    assert payloads
    assert {stat.S_IMODE(path.stat().st_mode) for path in payloads} == {0o555}
    assert stat.S_IMODE((target / ".complete").stat().st_mode) == 0o444
    assert {stat.S_IMODE(path.stat().st_mode) for path in directories} == {0o555}


def test_wheel_tolerates_unhashed_rows_stale_rows_signatures_and_reordering(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("record-tolerance", "1.0", {"record_tolerance.py": "VALUE = 1\n"})

    def transform(rows: list[list[str]]) -> list[list[str]]:
        next(row for row in rows if row[0] == "record_tolerance.py")[1] = ""
        rows.append(["stale-but-safe.py", "sha256=" + "A" * 43, "999"])
        return list(reversed(rows))

    _rewrite_record(
        wheel,
        transform,
        added_members={
            "record_tolerance-1.0.dist-info/RECORD.jws": b"legacy signature",
            "record_tolerance-1.0.dist-info/RECORD.p7s": b"legacy signature",
        },
    )

    target = tmp_path / "out"
    extract_wheel(wheel, target)

    assert (target / "purelib" / "record_tolerance.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    marker = json.loads((target / ".complete").read_text(encoding="utf-8"))
    assert "purelib/record_tolerance-1.0.dist-info/RECORD.jws" in marker["file_hashes"]
    assert "purelib/record_tolerance-1.0.dist-info/RECORD.p7s" in marker["file_hashes"]


def test_wheel_sha512_record_hashes_are_verified(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("sha512-demo", "1.0", {"sha512_demo.py": "VALUE = 1\n"})
    with zipfile.ZipFile(wheel) as archive:
        members = {info.filename: archive.read(info) for info in archive.infolist()}
        record_name = "sha512_demo-1.0.dist-info/RECORD"
        rows = list(csv.reader(members[record_name].decode("utf-8").splitlines()))
    for row in rows:
        if not row[1]:
            continue
        digest = hashlib.sha512(members[row[0]]).digest()
        row[1] = "sha512=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerows(rows)
    members[record_name] = stream.getvalue().encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)

    extract_wheel(wheel, tmp_path / "out")

    assert (tmp_path / "out" / "purelib" / "sha512_demo.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_wheel_rejects_insecure_record_hashes(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("record-demo", "1.0", {"record_demo.py": "VALUE = 1\n"})
    with zipfile.ZipFile(wheel) as archive:
        members = {info.filename: archive.read(info) for info in archive.infolist()}
        record_name = "record_demo-1.0.dist-info/RECORD"
        rows = list(csv.reader(members[record_name].decode("utf-8").splitlines()))
    next(row for row in rows if row[0] == "record_demo.py")[1] = "sha1=invalid"
    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerows(rows)
    members[record_name] = stream.getvalue().encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)

    with pytest.raises(IntegrityError, match="Unsupported or insecure"):
        extract_wheel(wheel, tmp_path / "out")


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        (1, "sha256=invalid!", "Malformed wheel RECORD hash"),
        (2, "not-a-size", "Malformed wheel RECORD size"),
    ],
)
def test_wheel_rejects_malformed_record_fields(
    tmp_path: Path,
    wheel_factory,
    field: int,
    replacement: str,
    message: str,
) -> None:
    wheel = wheel_factory("malformed-record", "1.0", {"malformed_record.py": "VALUE = 1\n"})

    def transform(rows: list[list[str]]) -> list[list[str]]:
        next(row for row in rows if row[0] == "malformed_record.py")[field] = replacement
        return rows

    _rewrite_record(wheel, transform)
    with pytest.raises(IntegrityError, match=message):
        extract_wheel(wheel, tmp_path / "out")


@pytest.mark.parametrize("mismatch", ("hash", "size"))
def test_wheel_rejects_recorded_member_mismatches(tmp_path: Path, wheel_factory, mismatch: str) -> None:
    wheel = wheel_factory("record-mismatch", "1.0", {"record_mismatch.py": "VALUE = 1\n"})

    def transform(rows: list[list[str]]) -> list[list[str]]:
        row = next(row for row in rows if row[0] == "record_mismatch.py")
        row[1 if mismatch == "hash" else 2] = "sha256=" + "A" * 43 if mismatch == "hash" else "999"
        return rows

    _rewrite_record(wheel, transform)
    with pytest.raises(IntegrityError, match=f"RECORD {mismatch} mismatch"):
        extract_wheel(wheel, tmp_path / "out")


def test_wheel_rejects_missing_record(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("missing-record", "1.0", {"missing_record.py": "VALUE = 1\n"})
    with zipfile.ZipFile(wheel) as archive:
        members = {
            info.filename: archive.read(info)
            for info in archive.infolist()
            if not info.filename.endswith(".dist-info/RECORD")
        }
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with pytest.raises(IntegrityError, match="exactly one top-level RECORD"):
        extract_wheel(wheel, tmp_path / "out")


def test_wheel_rejects_duplicate_record_rows(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("duplicate-record", "1.0", {"duplicate_record.py": "VALUE = 1\n"})
    _rewrite_record(wheel, lambda rows: [*rows, rows[0]])
    with pytest.raises(IntegrityError, match="Duplicate wheel RECORD path"):
        extract_wheel(wheel, tmp_path / "out")


def test_wheel_rejects_malformed_record_encoding(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("encoded-record", "1.0", {"encoded_record.py": "VALUE = 1\n"})
    with zipfile.ZipFile(wheel) as archive:
        members = {info.filename: archive.read(info) for info in archive.infolist()}
    record_name = next(name for name in members if name.endswith(".dist-info/RECORD"))
    members[record_name] = b"\xff"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with pytest.raises(IntegrityError, match="Malformed wheel RECORD encoding"):
        extract_wheel(wheel, tmp_path / "out")


def test_wheel_rejects_file_directory_namespace_collision(tmp_path: Path) -> None:
    wheel = tmp_path / "collision-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("collision", b"file")
        archive.writestr("collision/child.py", b"child")
    with pytest.raises(CacheError, match="file/directory namespace collision"):
        extract_wheel(wheel, tmp_path / "out")


def test_wheel_link_metadata_is_safely_materialized_as_regular_file(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "link-metadata-demo",
        "1.0",
        {"link_metadata_demo.py": "link_metadata_target.py", "link_metadata_target.py": "VALUE = 1\n"},
    )
    rewritten = tmp_path / "rewritten.zip"
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(rewritten, "w") as destination:
        for source_info in source.infolist():
            data = source.read(source_info)
            if source_info.filename == "link_metadata_demo.py":
                target_info = zipfile.ZipInfo(source_info.filename)
                target_info.create_system = 3
                target_info.external_attr = (stat.S_IFLNK | 0o777) << 16
                destination.writestr(target_info, data)
            else:
                destination.writestr(source_info, data)
    rewritten.replace(wheel)

    extract_wheel(wheel, tmp_path / "out")

    materialized = tmp_path / "out" / "purelib" / "link_metadata_demo.py"
    assert materialized.read_text(encoding="utf-8") == "link_metadata_target.py"
    assert materialized.is_file()
    assert not materialized.is_symlink()


def test_wheel_promotion_keeps_staging_root_writable_for_darwin(
    tmp_path: Path,
    wheel_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = wheel_factory("demo", "1.0", {"demo/__init__.py": "VALUE = 1\n"})
    destination = tmp_path / "targets" / "artifact" / "environment"
    real_replace = os.replace
    promoted_modes: list[int] = []

    def darwin_replace(source: str | bytes | os.PathLike[str] | os.PathLike[bytes], target) -> None:  # type: ignore[no-untyped-def]
        source_path = Path(source)
        mode = stat.S_IMODE(source_path.stat().st_mode)
        if source_path.is_dir() and not mode & stat.S_IWUSR:
            raise PermissionError(13, "Darwin refuses to rename a write-disabled directory", source_path)
        promoted_modes.append(mode)
        real_replace(source, target)

    monkeypatch.setattr("depfix.wheel.os.replace", darwin_replace)
    extract_wheel(wheel, destination)

    assert promoted_modes and promoted_modes[-1] & stat.S_IWUSR
    assert not stat.S_IMODE(destination.stat().st_mode) & stat.S_IWUSR
    assert (destination / "purelib" / "demo" / "__init__.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert list(destination.parent.iterdir()) == [destination]
