from __future__ import annotations

import contextlib
import importlib.machinery
import json
import os
import py_compile
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import build_index, build_wheel, file_spec

import depfix
from depfix import cache as cache_module
from depfix.cache import Cache
from depfix.cli import main as cli_main
from depfix.config import ImportDeclaration, ProjectConfig
from depfix.errors import CacheError, OfflineArtifactMissingError, SpecifierError
from depfix.manager import reset_runtime_state
from depfix.manifest import write_manifest
from depfix.project import install_manifest, verify_manifest
from depfix.resolver import Resolver
from depfix.runtime import DepfixRuntime
from depfix.settings import reset_configuration, resolve_settings
from depfix.sync import sync_graph


@pytest.fixture(autouse=True)
def _clean_process_state():
    reset_configuration()
    reset_runtime_state()
    yield
    reset_configuration()
    reset_runtime_state()


def _installed_package(tmp_path: Path, wheel_factory):  # type: ignore[no-untyped-def]
    wheel = wheel_factory("cache-demo", "1.2.3", {"cache_demo.py": "VALUE = 7\n"})
    cache_dir = tmp_path / "cache"
    cache = Cache(cache_dir)
    graph = Resolver(cache).resolve(
        ProjectConfig(
            tmp_path / ".depfix" / "config.toml",
            (ImportDeclaration("demo", file_spec(wheel), "cache_demo"),),
            {},
        )
    )
    sync_graph(graph, cache, offline=True)
    return cache_dir, cache, graph


def _age_installation(cache: Cache, digest: str, *, days: int) -> None:
    path = cache.root / "metadata" / "packages" / f"{digest}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["installed_at"] = time.time() - days * 24 * 60 * 60
    path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")


def _install_versions(tmp_path: Path, wheel_factory, versions: tuple[str, ...]):  # type: ignore[no-untyped-def]
    cache_dir = tmp_path / "cache"
    cache = Cache(cache_dir)
    graphs = []
    for index, version in enumerate(versions):
        wheel = wheel_factory("Range_Demo", version, {"range_demo.py": f"VALUE = {index}\n"})
        graph = Resolver(cache).resolve(
            ProjectConfig(
                tmp_path / f"config-{index}.toml",
                (ImportDeclaration(f"demo{index}", file_spec(wheel), "range_demo"),),
                {},
            )
        )
        sync_graph(graph, cache, offline=True)
        graphs.append(graph)
    return cache_dir, cache, tuple(graphs)


def test_inventory_records_installation_size_and_successful_import_use(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]

    installed = cache.list_packages()
    assert len(installed) == 1
    assert installed[0].distribution == "cache-demo"
    assert installed[0].version == "1.2.3"
    assert installed[0].artifact_hash == artifact.sha256
    assert installed[0].last_used_at is None
    assert installed[0].size_bytes > 0
    assert not cache.has_blob(artifact.sha256)

    runtime = DepfixRuntime(graph, cache).activate()
    assert runtime.import_for_node(graph.nodes[0].id, "cache_demo").VALUE == 7
    used = cache.list_packages()[0]
    assert used.active is True
    runtime.deactivate()

    assert used.installed_at == installed[0].installed_at
    assert used.last_used_at is not None
    assert used.last_used_at >= used.installed_at
    assert cache.list_packages()[0].active is False


def test_record_artifact_upgrades_legacy_identity_without_resetting_installation_age(
    tmp_path: Path,
    wheel_factory,
) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    path = cache.root / "metadata" / "packages" / f"{artifact.sha256}.json"
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy.pop("artifact")
    legacy["installed_at"] = 1234.5
    path.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")

    cache.record_artifact(artifact)

    upgraded = json.loads(path.read_text(encoding="utf-8"))
    assert upgraded["installed_at"] == 1234.5
    assert upgraded["artifact"]["sha256"] == artifact.sha256
    assert upgraded["artifact"]["source_kind"] == artifact.source_kind


def test_cleanup_reclaims_stale_artifact_and_all_targets(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)

    result = cache.cleanup(30)

    assert [item.artifact_hash for item in result.removed] == [artifact.sha256]
    assert result.reclaimed_bytes > 0
    assert not cache.blob_path(artifact.sha256).exists()
    assert not (cache.root / "targets" / artifact.sha256).exists()
    assert cache.list_packages() == ()


def test_cleanup_repairs_read_only_tree_before_recursive_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    nested = target / "package"
    nested.mkdir(parents=True)
    module = nested / "module.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    module.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    nested.chmod(stat.S_IRUSR | stat.S_IXUSR)
    target.chmod(stat.S_IRUSR | stat.S_IXUSR)
    real_rmtree = cache_module.shutil.rmtree

    def assert_writable_then_remove(path, **kwargs):  # type: ignore[no-untyped-def]
        tree = Path(path)
        assert tree.stat().st_mode & stat.S_IWUSR
        assert all(item.stat().st_mode & stat.S_IWUSR for item in tree.rglob("*") if not item.is_symlink())
        real_rmtree(path, **kwargs)

    monkeypatch.setattr(cache_module.shutil, "rmtree", assert_writable_then_remove)

    cache_module._remove_path(target)

    assert not target.exists()


def test_remove_path_repairs_read_only_file_before_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"wheel")
    artifact.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    real_unlink = Path.unlink

    def assert_writable_then_unlink(path: Path, *, missing_ok: bool = False) -> None:
        assert path.stat().st_mode & stat.S_IWUSR
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", assert_writable_then_unlink)

    cache_module._remove_path(artifact)

    assert not artifact.exists()


def test_cleanup_skips_active_runtime_then_removes_after_release(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)
    runtime = DepfixRuntime(graph, cache).activate()

    protected = cache.cleanup(0)
    assert protected.removed == ()
    assert [item.artifact_hash for item in protected.skipped_active] == [artifact.sha256]

    runtime.deactivate()
    removed = cache.cleanup(0)
    assert [item.artifact_hash for item in removed.removed] == [artifact.sha256]


def test_concurrent_process_usage_transactions_merge_overlapping_artifacts(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    hashes = ("a" * 64, "b" * 64, "c" * 64)
    programs = [
        (
            "from pathlib import Path; from depfix.cache import Cache; "
            f"cache=Cache(Path({str(cache_dir)!r})); "
            f"[cache.record_usage(set({selected!r})) for _ in range(20)]"
        )
        for selected in ((hashes[0], hashes[1]), (hashes[1], hashes[2]))
    ]
    processes = [subprocess.Popen([sys.executable, "-c", program]) for program in programs]
    assert all(process.wait(timeout=10) == 0 for process in processes)

    store = Cache(cache_dir).root / "metadata" / "usage.sqlite3"
    with sqlite3.connect(store) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(usage)")]
        rows = connection.execute("SELECT artifact_hash, used_at FROM usage").fetchall()
    assert columns == ["artifact_hash", "used_at"]
    assert {row[0] for row in rows} == set(hashes)
    assert all(float(row[1]) > 0 for row in rows)


def test_large_usage_renewal_is_one_database_operation_without_artifact_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = Cache(tmp_path / "cache")
    hashes = {f"{value:064x}" for value in range(1000)}

    def forbidden_lock(_digest: str):  # type: ignore[no-untyped-def]
        raise AssertionError("usage renewal must not mutate per-artifact locks")

    monkeypatch.setattr(cache, "_artifact_lock", forbidden_lock)
    handle = cache.renew_usage(hashes, interval_seconds=3600)
    handle.close()

    with sqlite3.connect(cache.root / "metadata" / "usage.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM usage").fetchone() == (1000,)


def test_activation_fails_quickly_while_another_process_holds_usage_writer(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    store = cache.root / "metadata" / "usage.sqlite3"
    store.parent.mkdir(parents=True, exist_ok=True)
    program = (
        "import sqlite3, sys; "
        f"connection=sqlite3.connect({str(store)!r}, isolation_level=None); "
        "connection.execute('CREATE TABLE IF NOT EXISTS usage "
        "(artifact_hash TEXT PRIMARY KEY, used_at REAL NOT NULL)'); "
        "connection.execute('BEGIN IMMEDIATE'); print('ready', flush=True); "
        "sys.stdin.readline(); connection.rollback()"
    )
    writer = subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert writer.stdout is not None
        assert writer.stdout.readline().strip() == "ready"
        started = time.monotonic()
        with pytest.raises(CacheError, match="Unable to renew cache usage metadata"):
            DepfixRuntime(graph, cache).activate()
        assert time.monotonic() - started < 1.0
        assert cache.has_package(graph.artifacts[0].sha256)
    finally:
        assert writer.stdin is not None
        writer.stdin.write("release\n")
        writer.stdin.flush()
        assert writer.wait(timeout=5) == 0

    runtime = DepfixRuntime(graph, cache).activate()
    runtime.deactivate()


def test_periodic_usage_loop_performs_a_second_coalesced_renewal(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    digest = "a" * 64
    handle = cache.renew_usage({digest}, interval_seconds=1)
    store = cache.root / "metadata" / "usage.sqlite3"
    with sqlite3.connect(store) as connection:
        first = float(connection.execute("SELECT used_at FROM usage WHERE artifact_hash = ?", (digest,)).fetchone()[0])

    deadline = time.time() + 5
    renewed = first
    while renewed <= first and time.time() < deadline:
        time.sleep(0.05)
        with sqlite3.connect(store) as connection:
            renewed = float(
                connection.execute("SELECT used_at FROM usage WHERE artifact_hash = ?", (digest,)).fetchone()[0]
            )
    handle.close()

    assert renewed > first


def test_returning_graph_reservation_prevents_remove_then_reinstall(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)

    cache.reserve_artifacts({artifact.sha256})
    assert cache.cleanup(30).removed == ()
    assert [item.artifact_hash for item in cache.remove_package("cache-demo").skipped_active] == [artifact.sha256]
    assert cache.has_package(artifact.sha256)
    assert not cache.blob_path(artifact.sha256).exists()

    reservation = cache.root / "metadata" / "reservations" / f"{artifact.sha256}.touch"
    stale = time.time() - 2 * 60 * 60
    os.utime(reservation, (stale, stale))
    assert [item.artifact_hash for item in cache.cleanup(30).removed] == [artifact.sha256]


@pytest.mark.parametrize("first", ("installer", "uninstaller"))
def test_concurrent_uninstall_and_install_preserve_complete_reserved_target_in_both_lock_orders(
    tmp_path: Path,
    wheel_factory,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    selected = cache.list_packages()
    target_root = cache.root / "targets" / artifact.sha256
    for path in sorted(target_root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(stat.S_IRWXU)
    target_root.chmod(stat.S_IRWXU)
    shutil.rmtree(target_root)
    assert not cache.has_package(artifact.sha256)
    target_key = "target:" + artifact.id
    original_lock = cache.lock
    first_has_target = threading.Event()
    second_reached_target = threading.Event()
    release_first = threading.Event()
    failures: list[BaseException] = []
    uninstall_results = []
    materialized = threading.Event()

    @contextlib.contextmanager
    def coordinated_lock(key: str):
        actor = threading.current_thread().name
        if key == target_key and actor != first:
            second_reached_target.set()
        with original_lock(key):
            if key == target_key and actor == first:
                first_has_target.set()
                assert release_first.wait(timeout=5)
            yield

    monkeypatch.setattr(cache, "lock", coordinated_lock)
    original_target_is_complete = cache._target_is_complete

    def observe_materialization(target: Path, digest: str) -> bool:
        complete = original_target_is_complete(target, digest)
        if complete and threading.current_thread().name == "installer":
            materialized.set()
        return complete

    monkeypatch.setattr(cache, "_target_is_complete", observe_materialization)

    def install() -> None:
        try:
            cache.reserve_artifacts({artifact.sha256})
            sync_graph(graph, cache, offline=False)
        except BaseException as exc:
            failures.append(exc)

    def uninstall() -> None:
        try:
            uninstall_results.append(
                cache._remove_entries(
                    selected,
                    protected_hashes=set(),
                    dry_run=False,
                    protect_live_runtimes=True,
                )
            )
        except BaseException as exc:
            failures.append(exc)

    threads = {
        "installer": threading.Thread(target=install, name="installer"),
        "uninstaller": threading.Thread(target=uninstall, name="uninstaller"),
    }
    threads[first].start()
    assert first_has_target.wait(timeout=5)
    second = "uninstaller" if first == "installer" else "installer"
    threads[second].start()
    assert second_reached_target.wait(timeout=5)
    release_first.set()
    for thread in threads.values():
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert failures == []
    assert materialized.is_set()
    assert len(uninstall_results) == 1
    assert uninstall_results[0].removed == ()
    assert all(item.artifact_hash == artifact.sha256 for item in uninstall_results[0].skipped_active)
    assert cache.has_package(artifact.sha256)
    cache.verify_packages()


@pytest.mark.parametrize("first", ("activator", "uninstaller"))
def test_runtime_activation_and_uninstall_serialize_lease_validation_with_deletion(
    tmp_path: Path,
    wheel_factory,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    target_key = "target:" + artifact.id
    original_lock = cache.lock
    first_has_target = threading.Event()
    second_reached_target = threading.Event()
    release_first = threading.Event()
    runtime = DepfixRuntime(graph, cache)
    activation_errors: list[BaseException] = []
    uninstall_results = []

    @contextlib.contextmanager
    def coordinated_lock(key: str):
        actor = threading.current_thread().name
        if key == target_key and actor != first:
            second_reached_target.set()
        with original_lock(key):
            if key == target_key and actor == first:
                first_has_target.set()
                assert release_first.wait(timeout=5)
            yield

    monkeypatch.setattr(cache, "lock", coordinated_lock)

    def activate() -> None:
        try:
            runtime.activate()
        except BaseException as exc:
            activation_errors.append(exc)

    def uninstall() -> None:
        uninstall_results.append(cache.uninstall(("cache-demo",)))

    threads = {
        "activator": threading.Thread(target=activate, name="activator"),
        "uninstaller": threading.Thread(target=uninstall, name="uninstaller"),
    }
    threads[first].start()
    assert first_has_target.wait(timeout=5)
    second = "uninstaller" if first == "activator" else "activator"
    threads[second].start()
    second_reached = second_reached_target.wait(timeout=5)
    release_first.set()
    assert second_reached
    for thread in threads.values():
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert len(uninstall_results) == 1
    if first == "activator":
        assert activation_errors == []
        assert uninstall_results[0].removed == ()
        assert [item.artifact_hash for item in uninstall_results[0].skipped_active] == [artifact.sha256]
        assert cache.has_package(artifact.sha256)
        runtime.deactivate()
    else:
        assert [item.artifact_hash for item in uninstall_results[0].removed] == [artifact.sha256]
        assert len(activation_errors) == 1
        assert isinstance(activation_errors[0], CacheError)
        assert not cache.has_package(artifact.sha256)


def test_python_and_cli_cache_inventory_cleanup_and_removal(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]

    assert depfix.list_cached_packages(cache_dir=cache_dir) == cache.list_packages()
    exit_code = cli_main(["--json", "--cache-dir", str(cache_dir), "list"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload[0]["distribution"] == "cache-demo"
    assert payload[0]["size_bytes"] > 0
    assert payload[0]["active"] is False

    exit_code = cli_main(["--cache-dir", str(cache_dir), "cache", "list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cache-demo" in captured.out
    assert "'cache list' is deprecated; use 'depfix list'" in captured.err

    preview = depfix.remove_cached_package("cache-demo", version="1.2.3", cache_dir=cache_dir, dry_run=True)
    assert preview.dry_run is True and len(preview.removed) == 1
    assert cache.has_package(artifact.sha256)
    assert not cache.has_blob(artifact.sha256)

    exit_code = cli_main(
        [
            "--json",
            "--cache-dir",
            str(cache_dir),
            "cache",
            "remove",
            "cache-demo",
            "--version",
            "1.2.3",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["removed"][0]["artifact_hash"] == artifact.sha256
    assert depfix.list_cached_packages(cache_dir=cache_dir) == ()


def test_uninstall_supports_bare_exact_range_compound_and_normalized_names(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, _graphs = _install_versions(tmp_path, wheel_factory, ("0.9.0", "1.0.0", "1.2.3", "2.0.0"))

    result = cache.uninstall(("range_demo>=1.0,!=1.2.3,<2", "Range.Demo==2.0.0"))

    assert [package.version for package in result.matched] == ["1.0.0", "2.0.0"]
    assert [package.version for package in result.removed] == ["1.0.0", "2.0.0"]
    assert [package.version for package in cache.list_packages()] == ["0.9.0", "1.2.3"]
    assert [selection.normalized for selection in result.specifiers] == [
        "range-demo!=1.2.3,<2,>=1.0",
        "range-demo==2.0.0",
    ]

    bare = cache.uninstall(("range-demo",))
    assert [package.version for package in bare.removed] == ["0.9.0", "1.2.3"]
    assert cache.list_packages() == ()


def test_uninstall_deduplicates_overlapping_specifiers_and_reports_json_no_match_and_dry_run(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir, cache, _graphs = _install_versions(tmp_path, wheel_factory, ("1.0.0", "1.5.0", "2.0.0"))
    before = {str(path.relative_to(cache.root)): path.read_bytes() for path in cache.root.rglob("*") if path.is_file()}

    exit_code = cli_main(
        [
            "--json",
            "--cache-dir",
            str(cache_dir),
            "uninstall",
            "range-demo>=1",
            "range-demo==1.5.0",
            "missing-demo",
            "--dry-run",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert len(payload["matched"]) == 3
    assert len(payload["removed"]) == 3
    assert payload["dry_run"] is True
    assert payload["specifiers"][2]["matched_artifacts"] == []
    assert cache.list_packages() and len(cache.list_packages()) == 3
    after = {str(path.relative_to(cache.root)): path.read_bytes() for path in cache.root.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize(
    "specifier",
    ("range-demo[extra]", "range-demo; python_version > '3'", "range-demo @ https://example.test/a.whl", "not a req"),
)
def test_uninstall_rejects_non_distribution_selectors_with_guidance(
    tmp_path: Path, wheel_factory, capsys: pytest.CaptureFixture[str], specifier: str
) -> None:
    cache_dir, _cache, _graphs = _install_versions(tmp_path, wheel_factory, ("1.0.0",))

    assert cli_main(["--cache-dir", str(cache_dir), "uninstall", specifier]) == 2
    assert "use only" in capsys.readouterr().err.lower()


def test_uninstall_protects_active_runtime_and_preserves_shared_dependencies(tmp_path: Path, wheel_factory) -> None:
    dependency = wheel_factory("shared-dependency", "1.0.0", {"shared_dependency.py": "VALUE = 1\n"})
    root = wheel_factory(
        "uninstall-root",
        "1.0.0",
        {"uninstall_root.py": "VALUE = 1\n"},
        requires=["shared-dependency==1.0.0"],
    )
    cache_dir = tmp_path / "cache"
    cache = Cache(cache_dir)
    index = build_index(tmp_path / "index", [dependency])
    graph = Resolver(cache, index_url=index).resolve(
        ProjectConfig(
            tmp_path / "config.toml",
            (ImportDeclaration("root", file_spec(root), "uninstall_root"),),
            {},
        )
    )
    sync_graph(graph, cache, offline=False)
    runtime = DepfixRuntime(graph, cache).activate()
    try:
        protected = cache.uninstall(("uninstall-root",))
        assert protected.removed == ()
        assert [package.distribution for package in protected.skipped_active] == ["uninstall-root"]
    finally:
        runtime.deactivate()

    removed = cache.uninstall(("uninstall-root",))
    assert [package.distribution for package in removed.removed] == ["uninstall-root"]
    assert [package.distribution for package in cache.list_packages()] == ["shared-dependency"]
    assert cache.inventory().installations == ()


def test_uninstall_honors_a_runtime_lease_from_another_process(tmp_path: Path, wheel_factory) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    digest = graph.artifacts[0].sha256
    program = (
        "from pathlib import Path; from depfix.cache import Cache; "
        f"handle=Cache(Path({str(cache_dir)!r})).renew_usage({{{digest!r}}}, interval_seconds=3600); "
        "print('ready', flush=True); input(); handle.close()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    try:
        result = cache.uninstall(("cache-demo",))
        assert result.removed == ()
        assert [package.artifact_hash for package in result.skipped_active] == [digest]
    finally:
        assert process.stdin is not None
        process.stdin.write("\n")
        process.stdin.flush()
        assert process.wait(timeout=10) == 0

    assert [package.artifact_hash for package in cache.uninstall(("cache-demo",)).removed] == [digest]


def test_uninstall_preserves_exact_manifest_and_offline_reuse_fails_clearly(tmp_path: Path, wheel_factory) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    manifest = tmp_path / "imports.lock"
    write_manifest(graph, manifest)
    manifest_before = manifest.read_bytes()

    assert cache.uninstall(("cache-demo",)).removed
    assert manifest.read_bytes() == manifest_before
    with pytest.raises(OfflineArtifactMissingError, match="unavailable offline"):
        install_manifest(manifest, frozen=True, offline=True, cache_dir=cache_dir)


def test_inventory_exposes_command_provenance_and_dependency_trees(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dependency = wheel_factory("inventory-dependency", "2.0.0", {"inventory_dependency.py": "VALUE = 2\n"})
    root = wheel_factory(
        "inventory-root",
        "1.0.0",
        {"inventory_root.py": "VALUE = 1\n"},
        requires=["inventory-dependency==2.0.0"],
    )
    index = build_index(tmp_path / "index", [dependency])
    cache_dir = tmp_path / "cache"
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(file_spec(root) + "\n", encoding="utf-8")
    command = shlex.join(("depfix", "pip", "install", "-r", str(requirements.resolve())))

    assert (
        cli_main(
            [
                "pip",
                "install",
                "-r",
                str(requirements),
                "--index-url",
                index,
                "--cache-dir",
                str(cache_dir),
                "--quiet",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""

    inventory = depfix.inspect_cache(cache_dir=cache_dir)
    assert {package.distribution for package in inventory.packages} == {
        "inventory-dependency",
        "inventory-root",
    }
    assert inventory.total_size_bytes == sum(package.size_bytes for package in inventory.packages)
    assert len(inventory.installations) == 1
    installation = inventory.installations[0]
    assert installation.reason.command == command
    assert installation.roots[0].package.distribution == "inventory-root"
    assert installation.roots[0].dependencies[0].package.distribution == "inventory-dependency"
    assert all(package.reasons[0].command == command for package in inventory.packages)

    exit_code = cli_main(["--cache-dir", str(cache_dir), "list"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "inventory-root==1.0.0" in output
    assert "inventory-dependency==2.0.0" in output
    assert "inventory-root" in output
    assert "inventory-dependency" in output
    assert command in output

    exit_code = cli_main(["--cache-dir", str(cache_dir), "tree"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert command in output
    assert "└── inventory-root==1.0.0" in output
    assert "inventory-dependency==2.0.0" in output

    exit_code = cli_main(["tree", "--cache-dir", str(cache_dir), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload[0]["reason"]["command"] == command
    assert payload[0]["roots"][0]["dependencies"][0]["package"]["distribution"] == "inventory-dependency"


def test_inventory_reports_code_locations_and_same_version_artifact_variants(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = build_wheel(first_dir, "variant-demo", "1.0.0", {"variant_demo.py": "BUILD = 1\n"})
    second = build_wheel(second_dir, "variant-demo", "1.0.0", {"variant_demo.py": "BUILD = 2\n"})
    cache_dir = tmp_path / "cache"
    cache = Cache(cache_dir)
    depfix.configure(cache_dir=cache_dir, log_level="WARNING")

    first_line = sys._getframe().f_lineno + 1
    depfix.load_package(file_spec(first))
    depfix.load_package(file_spec(second))

    inventory = depfix.inspect_cache(cache_dir=cache_dir)
    assert len(inventory.packages) == 2
    assert len(inventory.duplicates) == 1
    duplicate = inventory.duplicates[0]
    assert duplicate.distribution == "variant-demo"
    assert duplicate.versions == ("1.0.0",)
    assert duplicate.same_version_variants == ("1.0.0",)
    assert duplicate.occurrences == 2
    assert duplicate.additional_size_bytes == duplicate.total_size_bytes - max(
        package.size_bytes for package in duplicate.packages
    )
    first_reason = next(
        package.reasons[0] for package in inventory.packages if "first" in package.reasons[0].description
    )
    assert Path(first_reason.source_file).resolve() == Path(__file__).resolve()
    assert first_reason.source_line == first_line

    exit_code = cli_main(["--cache-dir", str(cache_dir), "list", "--view", "duplicates"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "same-version variants: 1.0.0" in output

    protected = cache.uninstall(("variant-demo==1.0.0",))
    assert len(protected.skipped_active) == 2
    reset_runtime_state()
    stale = time.time() - 2 * 60 * 60
    for reservation in (cache.root / "metadata" / "reservations").glob("*.touch"):
        os.utime(reservation, (stale, stale))
    removed = cache.uninstall(("variant-demo==1.0.0",))
    assert len(removed.removed) == 2
    assert cache.list_packages() == ()
    assert "variant-demo — 2 artifacts" in output


def test_cached_resolutions_and_explicit_manifest_inspection(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    manifest = tmp_path / "imports.lock"
    write_manifest(graph, manifest)
    resolution = cache.root / "resolutions" / "request-identity" / "imports.lock"
    write_manifest(graph, resolution)

    assert cli_main(["--json", "--cache-dir", str(cache_dir), "cache", "resolutions"]) == 0
    resolutions = json.loads(capsys.readouterr().out)
    assert resolutions[0]["manifest_id"] == graph.graph_id
    assert resolutions[0]["packages"] == ["cache-demo==1.2.3"]
    assert resolutions[0]["requests"]
    assert "imports.lock" not in resolutions[0]

    assert cli_main(["--json", "list", "--manifest", str(manifest)]) == 0
    requests = json.loads(capsys.readouterr().out)
    assert requests[0]["alias"] == "demo"

    assert cli_main(["--json", "tree", "--manifest", str(manifest)]) == 0
    tree = json.loads(capsys.readouterr().out)
    assert tree["manifest_id"] == graph.graph_id
    assert tree["nodes"][0]["distribution"] == "cache-demo"

    assert cli_main(["--json", "list", str(manifest)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["alias"] == "demo"
    assert "positional manifest inspection is deprecated" in captured.err


def test_explicit_cleanup_is_immediate_and_automatic_cleanup_is_two_phase(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)

    preview = depfix.cleanup_cache(days=30, cache_dir=cache_dir, dry_run=True)
    assert [item.artifact_hash for item in preview.removed] == [artifact.sha256]
    assert cache.has_package(artifact.sha256)
    assert not cache.has_blob(artifact.sha256)

    exit_code = cli_main(["--json", "--cache-dir", str(cache_dir), "cache", "cleanup", "--days", "30"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["removed"][0]["artifact_hash"] == artifact.sha256

    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)
    clock = cache.root / "metadata" / "cleanup.touch"
    stale = time.time() - 25 * 60 * 60
    os.utime(clock, (stale, stale))

    automatic = cache.automatic_cleanup(30, protected_hashes=set())
    assert automatic is not None
    assert automatic.removed == ()
    assert [item.artifact_hash for item in automatic.pending_candidates] == [artifact.sha256]
    candidate = cache.root / "metadata" / "deletion-candidates" / f"{artifact.sha256}.json"
    data = json.loads(candidate.read_text(encoding="utf-8"))
    data["candidate_at"] = time.time() - 25 * 60 * 60
    candidate.write_text(json.dumps(data), encoding="utf-8")
    automatic = cache.automatic_cleanup(30, protected_hashes=set(), force=True)
    assert automatic is not None
    assert [item.artifact_hash for item in automatic.removed] == [artifact.sha256]


@pytest.mark.parametrize("candidate_at", [0, float("nan"), float("-inf"), float("inf")])
def test_automatic_cleanup_treats_invalid_candidate_clocks_conservatively(
    tmp_path: Path, wheel_factory, candidate_at: float
) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)
    candidate = cache.root / "metadata" / "deletion-candidates" / f"{artifact.sha256}.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        json.dumps(
            {
                "format_version": 1,
                "candidate_at": candidate_at,
                "last_relevant": cache.list_packages()[0].installed_at.timestamp(),
            }
        ),
        encoding="utf-8",
    )

    result = cache.automatic_cleanup(30, protected_hashes=set(), force=True)

    assert result is not None and result.removed == ()
    assert [item.artifact_hash for item in result.pending_candidates] == [artifact.sha256]
    assert cache.has_package(artifact.sha256)


def test_automatic_cleanup_treats_corrupt_and_future_candidates_conservatively(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)
    candidate = cache.root / "metadata" / "deletion-candidates" / f"{artifact.sha256}.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("{interrupted", encoding="utf-8")

    result = cache.automatic_cleanup(30, protected_hashes=set(), force=True)
    assert result is not None and result.removed == ()
    assert [item.artifact_hash for item in result.pending_candidates] == [artifact.sha256]
    data = json.loads(candidate.read_text(encoding="utf-8"))
    data["candidate_at"] = time.time() + 86400
    candidate.write_text(json.dumps(data), encoding="utf-8")

    result = cache.automatic_cleanup(30, protected_hashes=set(), force=True)
    assert result is not None and result.removed == ()
    assert [item.artifact_hash for item in result.pending_candidates] == [artifact.sha256]


def test_interrupted_candidate_persistence_leaves_artifact_and_recovers(
    tmp_path: Path, wheel_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)
    real_replace = cache_module.os.replace

    def interrupt_candidate(source, destination):  # type: ignore[no-untyped-def]
        if "deletion-candidates" in str(destination):
            raise OSError("simulated interrupted candidate write")
        real_replace(source, destination)

    monkeypatch.setattr(cache_module.os, "replace", interrupt_candidate)
    with pytest.raises(OSError, match="interrupted candidate write"):
        cache.automatic_cleanup(30, protected_hashes=set(), force=True)
    assert cache.has_package(artifact.sha256)
    assert not any((cache.root / "metadata" / "deletion-candidates").glob(".*.tmp"))

    monkeypatch.setattr(cache_module.os, "replace", real_replace)
    result = cache.automatic_cleanup(30, protected_hashes=set(), force=True)
    assert result is not None and len(result.pending_candidates) == 1
    assert cache.has_package(artifact.sha256)


def test_automatic_cleanup_fails_closed_for_a_corrupt_usage_store(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)
    cache.record_usage({artifact.sha256}, used_at=time.time() - 31 * 86400)
    store = cache.root / "metadata" / "usage.sqlite3"
    store.write_bytes(b"interrupted database")

    with pytest.raises(CacheError, match="Unable to read cache usage metadata"):
        cache.automatic_cleanup(30, protected_hashes=set(), force=True)
    assert cache.has_package(artifact.sha256)


def test_cache_retention_configuration_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / ".depfix"
    state.mkdir()
    (state / "config.toml").write_text(
        "[settings]\ncache-retention-days = 45\ncache-auto-cleanup = false\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert resolve_settings().cache_retention_days == 45
    assert resolve_settings().cache_auto_cleanup is False

    monkeypatch.setenv("DEPFIX_CACHE_RETENTION_DAYS", "20")
    monkeypatch.setenv("DEPFIX_CACHE_AUTO_CLEANUP", "1")
    assert resolve_settings().cache_retention_days == 20
    assert resolve_settings().cache_auto_cleanup is True

    depfix.configure(cache_retention_days=10, cache_auto_cleanup=False)
    configured = resolve_settings()
    assert configured.cache_retention_days == 10
    assert configured.cache_auto_cleanup is False
    assert resolve_settings(cache_retention_days=5, cache_auto_cleanup=True).cache_retention_days == 5

    with pytest.raises(ValueError, match="non-negative integer"):
        depfix.configure(cache_retention_days=-1)


def test_active_graph_renews_complete_transitive_closure_and_cancels_candidates(tmp_path: Path, wheel_factory) -> None:
    dependency = wheel_factory("renew-dependency", "1.0.0", {"renew_dependency.py": "VALUE = 1\n"})
    root = wheel_factory(
        "renew-root",
        "1.0.0",
        {"renew_root.py": "VALUE = 2\n"},
        requires=["renew-dependency==1.0.0"],
    )
    cache_dir = tmp_path / "cache"
    cache = Cache(cache_dir)
    index = build_index(tmp_path / "index", [dependency])
    graph = Resolver(cache, settings=resolve_settings(index_url=index, discover=False)).resolve(
        ProjectConfig(
            tmp_path / ".depfix" / "config.toml",
            (ImportDeclaration("root", file_spec(root), "renew_root"),),
            {},
        )
    )
    sync_graph(graph, cache)
    for artifact in graph.artifacts:
        _age_installation(cache, artifact.sha256, days=31)
    first = cache.automatic_cleanup(30, protected_hashes=set(), force=True)
    assert first is not None and len(first.pending_candidates) == 2

    runtime = DepfixRuntime(graph, cache).activate()
    store = cache.root / "metadata" / "usage.sqlite3"
    deadline = time.time() + 5
    recorded: set[str] = set()
    while time.time() < deadline:
        try:
            with sqlite3.connect(store) as connection:
                recorded = {row[0] for row in connection.execute("SELECT artifact_hash FROM usage")}
        except sqlite3.Error:
            pass
        if recorded == {artifact.sha256 for artifact in graph.artifacts}:
            break
        time.sleep(0.01)
    assert recorded == {artifact.sha256 for artifact in graph.artifacts}
    second = cache.automatic_cleanup(30, protected_hashes=set(), force=True)
    assert second is not None and second.removed == () and second.pending_candidates == ()
    assert not any((cache.root / "metadata" / "deletion-candidates").glob("*.json"))
    runtime.deactivate()


def test_renewal_wins_race_between_candidate_scan_and_eligible_deletion(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)
    first = cache.automatic_cleanup(30, protected_hashes=set(), force=True)
    assert first is not None and len(first.pending_candidates) == 1
    candidate = cache._candidate_path(artifact.sha256)
    data = json.loads(candidate.read_text(encoding="utf-8"))
    data["candidate_at"] = time.time() - 25 * 60 * 60
    candidate.write_text(json.dumps(data), encoding="utf-8")

    original_remove = cache._remove_automatic_entries

    def renew_then_remove(entries, **kwargs):  # type: ignore[no-untyped-def]
        cache.record_usage({artifact.sha256})
        return original_remove(entries, **kwargs)

    cache._remove_automatic_entries = renew_then_remove  # type: ignore[method-assign]
    result = cache.automatic_cleanup(30, protected_hashes=set(), force=True)

    assert result is not None and result.removed == ()
    assert [item.artifact_hash for item in result.skipped_active] == [artifact.sha256]
    assert cache.has_package(artifact.sha256)


def test_usage_renewal_does_not_mutate_read_only_image_origin_tree(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    target = cache.unpacked_path(artifact.id)
    original_modes = {path: path.stat().st_mode for path in (target, *target.rglob("*"))}
    for path in sorted(original_modes, key=lambda item: len(item.parts), reverse=True):
        path.chmod(stat.S_IRUSR | (stat.S_IXUSR if path.is_dir() else 0))

    cache.record_usage({artifact.sha256})

    assert cache.has_package(artifact.sha256)
    assert all(not (path.stat().st_mode & stat.S_IWUSR) for path in original_modes)


def test_global_cleanup_configuration_cli_and_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert (
        cli_main(
            [
                "--json",
                "config",
                "set",
                "--retention-days",
                "12",
                "--auto-cleanup",
                "false",
                "--renewal-seconds",
                "90",
                "--deletion-grace-hours",
                "6",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["cache_retention_days"] == 12
    assert payload["cache_auto_cleanup"] is False
    assert payload["cache_renewal_seconds"] == 90
    assert payload["cache_deletion_grace_hours"] == 6
    monkeypatch.setenv("DEPFIX_CACHE_RENEWAL_SECONDS", "30")
    assert resolve_settings(discover=False).cache_renewal_seconds == 30
    depfix.configure(cache_renewal_seconds=15)
    assert resolve_settings(discover=False).cache_renewal_seconds == 15
    with pytest.raises(SpecifierError, match="at least twice"):
        depfix.configure(cache_renewal_seconds=3600, cache_deletion_grace_hours=1)
    assert resolve_settings(discover=False).cache_renewal_seconds == 15
    assert cli_main(["config", "set", "--renewal-seconds", "3600", "--deletion-grace-hours", "1"]) == 2
    assert "at least twice" in capsys.readouterr().err


def test_windows_owner_probe_never_uses_terminating_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(cache_module, "_windows_pid_is_running", lambda _pid: True)

    def forbidden_kill(_pid: int, _signal: int) -> None:
        raise AssertionError("os.kill(pid, 0) terminates the target process on Windows")

    monkeypatch.setattr(os, "kill", forbidden_kill)
    assert cache_module._pid_is_running(os.getpid() + 100_000) is True


def test_install_reconciles_retained_archive_without_removing_package(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    retained = cache.blob_path(artifact.sha256)
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_bytes(b"obsolete duplicate")
    abandoned_staging = cache.unpacked_path(artifact.id).with_name(cache.unpacked_path(artifact.id).name + ".abandoned")
    abandoned_staging.mkdir()
    (abandoned_staging / "partial.py").write_text("PARTIAL = True\n", encoding="utf-8")

    sync_graph(graph, cache, offline=True)

    assert not retained.exists()
    assert not abandoned_staging.exists()
    assert cache.has_package(artifact.sha256)
    runtime = DepfixRuntime(graph, cache).activate()
    try:
        assert runtime.import_for_node(graph.nodes[0].id, "cache_demo").VALUE == 7
    finally:
        runtime.deactivate()


def test_package_verification_detects_deleted_materialized_payload(tmp_path: Path, wheel_factory) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    payload = cache.unpacked_path(artifact.id) / "purelib" / "cache_demo.py"

    payload.parent.chmod(stat.S_IRWXU)
    payload.chmod(stat.S_IRUSR | stat.S_IWUSR)
    payload.unlink()

    assert not cache.has_package(artifact.sha256)
    manifest = cache_dir / "imports.lock"
    write_manifest(graph, manifest)
    with pytest.raises(CacheError, match="not completely materialized"):
        verify_manifest(manifest, cache_dir=cache_dir)


@pytest.mark.parametrize("filename", ["injected.py", "evil.pyc"])
def test_package_verification_detects_unmanifested_materialized_payload(
    tmp_path: Path, wheel_factory, filename: str
) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    purelib = cache.unpacked_path(artifact.id) / "purelib"

    purelib.chmod(stat.S_IRWXU)
    injected = purelib / filename
    injected.write_text("INJECTED = True\n", encoding="utf-8")

    assert not cache.has_package(artifact.sha256)
    with pytest.raises(CacheError, match="incomplete"):
        cache.verify_packages()


def test_package_verification_rejects_unmanifested_namespace_directory(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    purelib = cache.unpacked_path(artifact.id) / "purelib"

    purelib.chmod(stat.S_IRWXU)
    (purelib / "injected_namespace").mkdir()

    injected = importlib.machinery.PathFinder.find_spec("injected_namespace", [str(purelib)])
    assert injected is not None and injected.submodule_search_locations is not None
    assert not cache.has_package(artifact.sha256)


def test_package_verification_accepts_authenticated_interpreter_bytecode(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    purelib = cache.unpacked_path(artifact.id) / "purelib"

    purelib.chmod(stat.S_IRWXU)
    bytecode = purelib / "__pycache__" / "cache_demo.cpython-311.pyc"
    bytecode.parent.mkdir()
    py_compile.compile(str(purelib / "cache_demo.py"), cfile=str(bytecode), doraise=True)

    assert cache.has_package(artifact.sha256)
    assert cache.verify_packages() == 1


def test_package_verification_rejects_bytecode_with_forged_source_header(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    purelib = cache.unpacked_path(artifact.id) / "purelib"

    purelib.chmod(stat.S_IRWXU)
    bytecode = purelib / "__pycache__" / "cache_demo.cpython-311.pyc"
    bytecode.parent.mkdir()
    py_compile.compile(str(purelib / "cache_demo.py"), cfile=str(bytecode), doraise=True)
    trusted_header = bytecode.read_bytes()[:16]

    forged_source = tmp_path / "forged.py"
    forged_bytecode = tmp_path / "forged.pyc"
    forged_source.write_text("VALUE = 9\n", encoding="utf-8")
    py_compile.compile(str(forged_source), cfile=str(forged_bytecode), doraise=True)
    bytecode.write_bytes(trusted_header + forged_bytecode.read_bytes()[16:])

    assert not cache.has_package(artifact.sha256)


def test_package_verification_rejects_derived_bytecode_with_trailing_bytes(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    purelib = cache.unpacked_path(artifact.id) / "purelib"

    purelib.chmod(stat.S_IRWXU)
    bytecode = purelib / "__pycache__" / "cache_demo.cpython-311.pyc"
    bytecode.parent.mkdir()
    py_compile.compile(str(purelib / "cache_demo.py"), cfile=str(bytecode), doraise=True)
    bytecode.write_bytes(bytecode.read_bytes() + b"unauthenticated trailer")

    assert not cache.has_package(artifact.sha256)


def test_package_verification_counts_manifested_wheel_bytecode(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "cache-bytecode",
        "1.2.3",
        {"cache_bytecode.py": "VALUE = 7\n", "cache_bytecode.pyc": b"wheel-owned bytecode"},
    )
    cache = Cache(tmp_path / "cache")
    graph = Resolver(cache).resolve(
        ProjectConfig(
            tmp_path / ".depfix" / "config.toml",
            (ImportDeclaration("demo", file_spec(wheel), "cache_bytecode"),),
            {},
        )
    )
    sync_graph(graph, cache, offline=True)
    artifact = graph.artifacts[0]
    bytecode = cache.unpacked_path(artifact.id) / "purelib" / "cache_bytecode.pyc"

    assert cache.has_package(artifact.sha256)
    bytecode.chmod(stat.S_IRUSR | stat.S_IWUSR)
    bytecode.write_bytes(b"mutated wheel-owned bytecode")
    assert not cache.has_package(artifact.sha256)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not generally available on Windows")
def test_package_verification_rejects_unmanifested_symlinked_package(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    purelib = cache.unpacked_path(artifact.id) / "purelib"
    external = tmp_path / "evil_package"
    external.mkdir()
    (external / "__init__.py").write_text("INJECTED = True\n", encoding="utf-8")

    purelib.chmod(stat.S_IRWXU)
    (purelib / "evil_package").symlink_to(external, target_is_directory=True)

    assert not cache.has_package(artifact.sha256)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are not available on this platform")
def test_package_verification_rejects_unmanifested_special_file(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    purelib = cache.unpacked_path(artifact.id) / "purelib"

    purelib.chmod(stat.S_IRWXU)
    os.mkfifo(purelib / "injected.fifo")

    assert not cache.has_package(artifact.sha256)


def test_cache_verify_reports_corrupt_target_omitted_from_inventory(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    payload_path = cache.unpacked_path(artifact.id) / "purelib" / "cache_demo.py"
    payload_path.parent.chmod(stat.S_IRWXU)
    payload_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    payload_path.unlink()

    assert cache.list_packages() == ()
    exit_code = cli_main(["--json", "--cache-dir", str(cache_dir), "cache", "verify"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["error"] == "CacheError"
    assert artifact.sha256 in payload["message"]


def test_prune_waits_for_active_artifact_reader(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    digest = "e" * 64
    blob = cache.blob_path(digest)
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"active input")
    locked = threading.Event()
    release = threading.Event()

    def hold_input() -> None:
        with cache._artifact_lock(digest):
            locked.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_input)
    holder.start()
    assert locked.wait(timeout=5)
    pruner = threading.Thread(target=lambda: cache.prune(set()))
    pruner.start()
    pruner.join(timeout=0.1)

    assert pruner.is_alive()
    assert blob.is_file()

    release.set()
    holder.join(timeout=5)
    pruner.join(timeout=5)
    assert not blob.exists()


def test_cleanup_removes_dead_download_but_preserves_live_owner(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    temporary = cache.root / "temp"
    temporary.mkdir(parents=True)
    stale = time.time() - 2 * 60 * 60
    dead = temporary / f"download-{os.getpid() + 100_000}-dead.part"
    live = temporary / f"download-{os.getpid()}-live.part"
    dead.write_bytes(b"dead")
    live.write_bytes(b"live")
    work = cache.root / "tmp"
    work.mkdir()
    abandoned_build = work / f"depfix-build-{os.getpid() + 100_000}-dead"
    abandoned_build.mkdir()
    abandoned_uv_cache = work / f"uv-cache-{os.getpid() + 100_000}-dead"
    abandoned_uv_cache.mkdir()
    live_uv_cache = work / f"uv-cache-{os.getpid()}-live"
    live_uv_cache.mkdir()
    os.utime(dead, (stale, stale))
    os.utime(live, (stale, stale))
    os.utime(abandoned_build, (stale, stale))
    os.utime(abandoned_uv_cache, (stale, stale))
    os.utime(live_uv_cache, (stale, stale))

    cache.cleanup(30)

    assert not dead.exists()
    assert live.exists()
    assert not abandoned_build.exists()
    assert not abandoned_uv_cache.exists()
    assert live_uv_cache.exists()


def test_cleanup_removes_stale_orphan_blob_and_legacy_download(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    digest = "a" * 64
    orphan = cache.blob_path(digest)
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"verified but never materialized")
    temporary = cache.root / "temp"
    temporary.mkdir(parents=True)
    legacy_download = temporary / "download-legacyname"
    legacy_download.write_bytes(b"interrupted legacy download")
    stale = time.time() - 2 * 24 * 60 * 60
    os.utime(orphan, (stale, stale))
    os.utime(legacy_download, (stale, stale))

    cache.cleanup(0, dry_run=True)

    assert orphan.exists()
    assert legacy_download.exists()

    cache.cleanup(0)

    assert not orphan.exists()
    assert not legacy_download.exists()
    assert cache.list_packages() == ()


def test_cleanup_preserves_recent_orphan_blob(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    digest = "b" * 64
    orphan = cache.blob_path(digest)
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"recent verified download")

    cache.cleanup(0)

    assert orphan.is_file()


def test_cleanup_dry_run_preserves_then_removes_stale_extraction_and_build_state(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    extraction_digest = "c" * 64
    extraction = cache.root / "targets" / extraction_digest / "environment.crashed"
    extraction.mkdir(parents=True)
    (extraction / "partial.py").write_text("PARTIAL = True\n", encoding="utf-8")
    build_digest = "d" * 64
    build = cache.root / "built-wheels" / build_digest
    build.mkdir(parents=True)
    (build / "package.whl").write_bytes(b"incomplete build")
    stale = time.time() - 2 * 24 * 60 * 60
    for path in (extraction / "partial.py", extraction, build / "package.whl", build):
        os.utime(path, (stale, stale))

    assert cache.list_packages() == ()
    dry_run = cache.cleanup(30, dry_run=True)

    assert dry_run.dry_run is True
    assert extraction.exists()
    assert build.exists()
    assert cache.list_packages() == ()

    cache.cleanup(30)

    assert not extraction.exists()
    assert not build.exists()
    assert cache.list_packages() == ()
