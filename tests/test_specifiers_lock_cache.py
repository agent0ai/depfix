from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest
from conftest import file_spec, sha256
from jsonschema import Draft202012Validator

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
        (Alias("demo", node.id, "demo", "pypi:demo==1.0"),),
        {"strict": True},
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
    assert load(path) == graph
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


def test_malicious_wheel_path_is_rejected(tmp_path: Path) -> None:
    wheel = tmp_path / "bad-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("../escape.py", "bad = True")
    with pytest.raises(CacheError, match="Unsafe path"):
        extract_wheel(wheel, tmp_path / "out")
    assert not (tmp_path / "escape.py").exists()


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
