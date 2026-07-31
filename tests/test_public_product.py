from __future__ import annotations

import json
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path

import pytest
from conftest import file_spec
from packaging.version import Version

import depfix
from depfix.cache import Cache, _validate_network_url
from depfix.cli import main as cli_main
from depfix.config import ImportDeclaration, ProjectConfig
from depfix.errors import CacheError, MultipleImportModulesError, NoImportModulesError, ResolutionError, SourceError
from depfix.manager import reset_runtime_state
from depfix.manifest import load_manifest, write_manifest
from depfix.project import create_bundle, export_project, install_manifest, verify_manifest
from depfix.resolver import Resolver, _extract_source_archive
from depfix.scanner import scan_project
from depfix.settings import Settings, reset_configuration, resolve_settings
from depfix.sources import parse_source
from depfix.sync import sync_graph
from depfix.uv_backend import UvBackend, UvExecutable
from depfix.wheel import inspect_wheel


@pytest.fixture(autouse=True)
def _clean_public_state():
    reset_configuration()
    reset_runtime_state()
    yield
    reset_configuration()
    reset_runtime_state()


def test_source_forms_normalize_without_confusing_git_authentication(tmp_path: Path) -> None:
    local = tmp_path / "module.py"
    local.write_text("VALUE = 1\n", encoding="utf-8")
    file_source = parse_source("file:module.py", base_dir=tmp_path)
    assert file_source.kind == "py" and file_source.path == local

    git = parse_source("git:https://user@github.example/acme/sdk.git@v2.4.0")
    assert git.url == "https://user@github.example/acme/sdk.git"
    assert git.requested_ref == "v2.4.0" and git.mutable
    pinned = parse_source("git:ssh://git@github.example/acme/sdk.git@1fadefa67b26508cc59cf38e6130bde2243c929d")
    assert pinned.commit == "1fadefa67b26508cc59cf38e6130bde2243c929d"
    direct = parse_source('acme-sdk[http] @ git+https://github.example/acme/sdk.git@v2.4.0 ; python_version >= "3.11"')
    assert direct.distribution == "acme-sdk"
    assert direct.extras == ("http",)
    assert "acme-sdk[http] @ git:" in direct.normalized
    assert "python_version" in direct.normalized


def test_core_metadata_and_artifact_module_discovery(tmp_path: Path, wheel_factory) -> None:
    renamed = wheel_factory(
        "PyYAML",
        "6.0.2",
        {"yaml/__init__.py": "VALUE = 1\n"},
        metadata_version="2.5",
        import_names=["yaml"],
    )
    assert inspect_wheel(renamed).public_modules == ("yaml",)

    namespace = wheel_factory(
        "cloud-storage",
        "1.0.0",
        {"google/cloud/storage/__init__.py": "VALUE = 1\n"},
        metadata_version="2.5",
        import_namespaces=["google", "google.cloud"],
    )
    inspected = inspect_wheel(namespace)
    assert inspected.public_modules == ("google.cloud.storage",)
    assert inspected.namespace_contributions == ("google", "google.cloud")

    command_only = wheel_factory(
        "command-only",
        "1.0.0",
        {"command_only_cli.py": "VALUE = 1\n"},
        metadata_version="2.5",
        import_names=[""],
    )
    assert inspect_wheel(command_only).public_modules == ()

    inconsistent = wheel_factory(
        "broken-import-metadata",
        "1.0.0",
        {"actual/__init__.py": "VALUE = 1\n"},
        metadata_version="2.5",
        import_names=["missing"],
    )
    with pytest.raises(ResolutionError, match="inconsistent"):
        inspect_wheel(inconsistent)


def test_stable_module_and_lazy_package_handle_contracts(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "multi-root-tools",
        "1.0.0",
        {"first.py": "VALUE = 'first'\n", "second.py": "VALUE = 'second'\n"},
    )
    depfix.configure(cache_dir=tmp_path / "cache")
    with pytest.raises(MultipleImportModulesError) as captured:
        depfix.import_module(file_spec(wheel))
    assert captured.value.import_modules == ("first", "second")

    package = depfix.load_package(file_spec(wheel))
    assert isinstance(package, depfix.PackageHandle)
    assert package.module_names == ("first", "second")
    assert not any(
        getattr(module, "__depfix_node_id__", None) == package.realm_id
        for module in tuple(sys.modules.values())
        if module is not None
    )
    assert package.modules.first.VALUE == "first"
    assert package.modules["second"].VALUE == "second"
    with pytest.raises(MultipleImportModulesError):
        package.only_module()


def test_live_load_reports_preparation_and_warning_level_silences_it(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wheel = wheel_factory("progress-demo", "1.0.0", {"progress_demo.py": "VALUE = 1\n"})
    cache_dir = tmp_path / "cache"
    depfix.configure(cache_dir=cache_dir, log_level="INFO")

    assert depfix.import_module(file_spec(wheel), module="progress_demo").VALUE == 1
    visible = capsys.readouterr()
    assert visible.out == ""
    assert "depfix  resolve" in visible.err
    assert "depfix  prepare  1 artifact" in visible.err
    assert "depfix  ready    progress-demo==1.0.0" in visible.err

    reset_runtime_state()
    reset_configuration()
    depfix.configure(cache_dir=cache_dir, log_level="WARNING")
    assert depfix.import_module(file_spec(wheel), module="progress_demo").VALUE == 1
    muted = capsys.readouterr()
    assert muted.out == ""
    assert muted.err == ""


def test_uv_success_summary_is_forwarded_to_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = UvBackend(Settings(cache_dir=tmp_path / "cache", log_level="INFO"), Cache(tmp_path / "cache"))
    executable = UvExecutable(Path("/fake/uv"), Version("0.11.0"), "test")
    monkeypatch.setattr(backend, "ensure_available", lambda: executable)

    def completed(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="",
            stderr=(
                "Resolved 2 packages\n"
                "Installed 2 packages\n"
                " + demo==1.0.0\n"
                "Index https://user:password@example.test/simple\n"
            ),
        )

    monkeypatch.setattr(subprocess, "run", completed)
    backend.run(["pip", "install", "demo"], forward_output=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "depfix  uv       Resolved 2 packages" in captured.err
    assert "depfix  uv       Installed 2 packages" in captured.err
    assert "depfix  uv       + demo==1.0.0" in captured.err
    assert "password" not in captured.err
    assert "https://<redacted>@example.test/simple" in captured.err


def test_json_cli_suppresses_progress(tmp_path: Path, wheel_factory, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = wheel_factory("json-progress-demo", "1.0.0", {"json_progress_demo.py": "VALUE = 1\n"})

    result = cli_main(["fetch", file_spec(wheel), "--json", "--cache-dir", str(tmp_path / "cache")])

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert json.loads(captured.out)["name"] == "json-progress-demo"


def test_no_module_package_handle_is_still_representable(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "command-package",
        "1.0.0",
        {"command_wrapper.py": "VALUE = 1\n"},
        metadata_version="2.5",
        import_names=[""],
    )
    depfix.configure(cache_dir=tmp_path / "cache")
    with pytest.raises(NoImportModulesError):
        depfix.import_module(file_spec(wheel))
    package = depfix.load_package(file_spec(wheel))
    assert package.module_names == ()
    with pytest.raises(NoImportModulesError):
        package.only_module()


def test_settings_precedence_and_optional_project_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / ".depfix"
    state.mkdir()
    (state / "config.toml").write_text(
        '[resolver]\nindex-url = "https://config.example/simple"\noffline = true\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    configured = resolve_settings()
    assert configured.index_url == "https://config.example/simple"
    assert configured.offline is True

    monkeypatch.setenv("DEPFIX_INDEX_URL", "https://env.example/simple")
    monkeypatch.setenv("DEPFIX_OFFLINE", "0")
    environment = resolve_settings()
    assert environment.index_url == "https://env.example/simple"
    assert environment.offline is False

    depfix.configure(index_url="https://python.example/simple", offline=True)
    assert resolve_settings().index_url == "https://python.example/simple"
    explicit = resolve_settings(index_url="https://call.example/simple", offline=False)
    assert explicit.index_url == "https://call.example/simple"
    assert explicit.offline is False


def test_scanner_is_static_and_reports_dynamic_calls(tmp_path: Path) -> None:
    source = tmp_path / "application.py"
    source.write_text(
        "from depfix import import_module as versioned_import\n"
        'SPEC = "idna" + "==3.10"\n'
        "safe = versioned_import(SPEC)\n"
        "unsafe = versioned_import(read_spec())\n",
        encoding="utf-8",
    )
    result = scan_project(tmp_path)
    assert len(result.requests) == 1
    assert result.requests[0].normalized_specifier == "idna==3.10"
    assert result.requests[0].assignment == "safe"
    assert len(result.dynamic_requests) == 1
    assert "safe static string" in result.dynamic_requests[0].reason


def test_bundle_is_deterministic_and_installs_without_network(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("bundle-demo", "1.0.0", {"bundle_demo/__init__.py": "VALUE = 7\n"})
    cache_dir = tmp_path / "cache"
    cache = Cache(cache_dir)
    config = ProjectConfig(
        tmp_path / ".depfix" / "config.toml",
        (ImportDeclaration("demo", file_spec(wheel), "bundle_demo"),),
        {},
    )
    graph = Resolver(cache).resolve(config)
    sync_graph(graph, cache, offline=True)
    manifest = tmp_path / ".depfix" / "imports.lock"
    write_manifest(graph, manifest)
    first = create_bundle(manifest, tmp_path / "first.depfixbundle", cache_dir=cache_dir)
    second = create_bundle(manifest, tmp_path / "second.depfixbundle", cache_dir=cache_dir)
    assert first.bundle.read_bytes() == second.bundle.read_bytes()
    assert verify_manifest(first.bundle).complete

    installed = install_manifest(
        first.bundle,
        frozen=True,
        offline=True,
        cache_dir=tmp_path / "airgap-cache",
    )
    installed_graph = load_manifest(installed.manifest)
    assert installed_graph.graph_id == graph.graph_id
    depfix.configure(
        manifest=installed.manifest,
        cache_dir=tmp_path / "airgap-cache",
        frozen=True,
        offline=True,
    )
    assert depfix.import_module(file_spec(wheel), module="bundle_demo").VALUE == 7


def test_uv_dependency_is_located_without_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    settings = Settings(cache_dir=tmp_path / "cache")
    executable = UvBackend(settings, Cache(settings.cache_dir)).ensure_available(allow_bootstrap=False)
    assert executable.version >= Version("0.11.0")
    assert executable.path.parent == Path(sys.executable).absolute().parent
    assert executable.source == "Depfix runtime dependency"


def test_depfix_pip_version_reports_the_uv_backend() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "depfix", "pip", "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("uv ")


@pytest.mark.parametrize("members", [("pkg/data.txt", "pkg/data.txt"), ("pkg/Data.txt", "pkg/data.txt")])
def test_source_archives_reject_duplicate_and_case_colliding_paths(
    tmp_path: Path,
    members: tuple[str, str],
) -> None:
    source = tmp_path / "unsafe.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr(members[0], "first")
            archive.writestr(members[1], "second")
    with pytest.raises(SourceError, match="duplicate or case-colliding"):
        _extract_source_archive(source, tmp_path / "out")


def test_network_policy_rejects_insecure_and_unlisted_artifact_hosts() -> None:
    with pytest.raises(CacheError, match="HTTPS"):
        _validate_network_url("http://packages.example/demo.whl", allowed_hosts=(), allow_insecure=False)
    with pytest.raises(CacheError, match="not permitted"):
        _validate_network_url(
            "https://elsewhere.example/demo.whl",
            allowed_hosts=("*.packages.example",),
            allow_insecure=False,
        )
    _validate_network_url(
        "https://cdn.packages.example/demo.whl",
        allowed_hosts=("*.packages.example",),
        allow_insecure=False,
    )


def test_export_serializes_network_policy_and_source_provenance(
    tmp_path: Path,
    wheel_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = wheel_factory("policy-demo", "1.0.0", {"policy_demo.py": "VALUE = 1\n"})
    (tmp_path / "application.py").write_text(
        f'from depfix import import_module\nmodule = import_module("{file_spec(wheel)}")\n',
        encoding="utf-8",
    )
    state = tmp_path / ".depfix"
    state.mkdir()
    (state / "config.toml").write_text(
        "[policy]\n"
        'allowed-hosts = ["pypi.org", "files.pythonhosted.org"]\n'
        'allowed-indexes = ["https://pypi.org/pypi"]\n'
        "allow-insecure-transport = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEPFIX_CACHE_DIR", str(tmp_path / "cache"))
    exported = export_project(tmp_path)
    graph = load_manifest(exported.manifest)
    assert graph.policy["allowed-hosts"] == ["pypi.org", "files.pythonhosted.org"]
    assert graph.artifacts[0].source_sha256 == graph.artifacts[0].sha256
    assert graph.artifacts[0].source_url.startswith("file:")
