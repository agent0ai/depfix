from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import file_spec

import depfix
from depfix import cache as cache_module
from depfix.cache import Cache
from depfix.cli import main as cli_main
from depfix.config import ImportDeclaration, ProjectConfig
from depfix.manager import reset_runtime_state
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


def test_inventory_records_installation_size_and_successful_import_use(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]

    installed = cache.list_packages()
    assert len(installed) == 1
    assert installed[0].distribution == "cache-demo"
    assert installed[0].version == "1.2.3"
    assert installed[0].artifact_hash == artifact.sha256
    assert installed[0].last_used_at is None
    assert installed[0].size_bytes > artifact.size

    runtime = DepfixRuntime(graph, cache).activate()
    assert runtime.import_for_node(graph.nodes[0].id, "cache_demo").VALUE == 7
    used = cache.list_packages()[0]
    runtime.deactivate()

    assert used.installed_at == installed[0].installed_at
    assert used.last_used_at is not None
    assert used.last_used_at >= used.installed_at


def test_cleanup_reclaims_stale_artifact_and_all_targets(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)

    result = cache.cleanup(30)

    assert [item.artifact_hash for item in result.removed] == [artifact.sha256]
    assert result.reclaimed_bytes > artifact.size
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


def test_cleanup_skips_active_runtime_then_removes_after_release(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)
    runtime = DepfixRuntime(graph, cache).activate()

    protected = cache.cleanup(30)
    assert protected.removed == ()
    assert [item.artifact_hash for item in protected.skipped_active] == [artifact.sha256]

    runtime.deactivate()
    removed = cache.cleanup(30)
    assert [item.artifact_hash for item in removed.removed] == [artifact.sha256]


def test_cleanup_honors_a_lease_from_another_process(tmp_path: Path, wheel_factory) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)
    code = (
        "from pathlib import Path; from depfix.cache import Cache; "
        f"lease = Cache(Path({str(cache_dir)!r})).lease({{{artifact.sha256!r}}}); "
        "print('ready', flush=True); input(); lease.close()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    try:
        protected = cache.cleanup(30)
        assert [item.artifact_hash for item in protected.skipped_active] == [artifact.sha256]
    finally:
        assert process.stdin is not None
        process.stdin.write("\n")
        process.stdin.flush()
        assert process.wait(timeout=10) == 0

    assert [item.artifact_hash for item in cache.cleanup(30).removed] == [artifact.sha256]


def test_returning_graph_reservation_prevents_remove_then_reinstall(tmp_path: Path, wheel_factory) -> None:
    _cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)

    cache.reserve_artifacts({artifact.sha256})
    assert cache.cleanup(30).removed == ()
    assert [item.artifact_hash for item in cache.remove_package("cache-demo").skipped_active] == [artifact.sha256]
    assert cache.blob_path(artifact.sha256).is_file()

    reservation = cache.root / "metadata" / "reservations" / f"{artifact.sha256}.touch"
    stale = time.time() - 2 * 60 * 60
    os.utime(reservation, (stale, stale))
    assert [item.artifact_hash for item in cache.cleanup(30).removed] == [artifact.sha256]


def test_python_and_cli_cache_inventory_cleanup_and_removal(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]

    assert depfix.list_cached_packages(cache_dir=cache_dir) == cache.list_packages()
    exit_code = cli_main(["--json", "--cache-dir", str(cache_dir), "cache", "list"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload[0]["distribution"] == "cache-demo"
    assert payload[0]["size_bytes"] > artifact.size

    preview = depfix.remove_cached_package("cache-demo", version="1.2.3", cache_dir=cache_dir, dry_run=True)
    assert preview.dry_run is True and len(preview.removed) == 1
    assert cache.has_blob(artifact.sha256)

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


def test_explicit_and_daily_cleanup_use_the_same_retention_contract(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir, cache, graph = _installed_package(tmp_path, wheel_factory)
    artifact = graph.artifacts[0]
    _age_installation(cache, artifact.sha256, days=31)

    preview = depfix.cleanup_cache(days=30, cache_dir=cache_dir, dry_run=True)
    assert [item.artifact_hash for item in preview.removed] == [artifact.sha256]
    assert cache.has_blob(artifact.sha256)

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
    assert [item.artifact_hash for item in automatic.removed] == [artifact.sha256]


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


def test_windows_lease_probe_never_uses_terminating_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(cache_module, "_windows_pid_is_running", lambda _pid: True)

    def forbidden_kill(_pid: int, _signal: int) -> None:
        raise AssertionError("os.kill(pid, 0) terminates the target process on Windows")

    monkeypatch.setattr(os, "kill", forbidden_kill)
    assert cache_module._pid_is_running(os.getpid() + 100_000) is True
