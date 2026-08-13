from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import subprocess
import sys
import sysconfig
import warnings
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
from conftest import build_index, file_spec
from packaging.version import Version

import depfix
from depfix.cache import Cache, _validate_network_url
from depfix.cli import main as cli_main
from depfix.config import ImportDeclaration, ProjectConfig
from depfix.errors import (
    CacheError,
    MultipleImportModulesError,
    NoImportModulesError,
    ResolutionError,
    SharedImportConflictError,
    SourceError,
    UnsafePackageError,
)
from depfix.manager import activate_manifest, load_generated_alias, reset_runtime_state
from depfix.manifest import load_manifest, write_manifest
from depfix.project import create_bundle, export_project, install_manifest, install_packages, verify_manifest
from depfix.resolver import Resolver, _extract_source_archive, _versions_equivalent
from depfix.scanner import scan_project
from depfix.settings import Settings, reset_configuration, resolve_settings
from depfix.sources import parse_source
from depfix.sync import sync_graph
from depfix.uv_backend import UvBackend, UvExecutable
from depfix.wheel import extract_wheel, inspect_wheel


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
    pinned = parse_source(
        "git:ssh://git@github.example/acme/sdk.git@1fadefa67b26508cc59cf38e6130bde2243c929d"  # pragma: allowlist secret
    )
    assert pinned.commit == "1fadefa67b26508cc59cf38e6130bde2243c929d"  # pragma: allowlist secret
    direct = parse_source('acme-sdk[http] @ git+https://github.example/acme/sdk.git@v2.4.0 ; python_version >= "3.11"')
    assert direct.distribution == "acme-sdk"
    assert direct.extras == ("http",)
    assert "acme-sdk[http] @ git:" in direct.normalized
    assert "python_version" in direct.normalized


def test_core_metadata_and_artifact_module_discovery(tmp_path: Path, wheel_factory) -> None:
    assert _versions_equivalent("13.0.3", "13.0.3.0")
    assert not _versions_equivalent("13.0.3", "13.0.4")

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

    vendored_metadata = wheel_factory(
        "vendored-metadata",
        "1.0.0",
        {
            "vendored_metadata.py": "VALUE = 1\n",
            "vendored_metadata/_vendor/helper-2.0.0.dist-info/METADATA": (
                "Metadata-Version: 2.3\nName: helper\nVersion: 2.0.0\n\n"
            ),
            "vendored_metadata/_vendor/helper-2.0.0.dist-info/RECORD": "",
        },
    )
    assert inspect_wheel(vendored_metadata).public_modules == ("vendored_metadata",)
    extract_wheel(vendored_metadata, tmp_path / "vendored-target")


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


def test_realm_provenance_and_opt_in_boundary_guards(tmp_path: Path, wheel_factory) -> None:
    legacy_wheel = wheel_factory(
        "boundary-demo",
        "1.0.0",
        {
            "boundary_demo.py": (
                "class Token:\n def __init__(self, value): self.value = value\ndef make(value): return Token(value)\n"
            )
        },
    )
    current_wheel = wheel_factory(
        "boundary-demo",
        "2.0.0",
        {
            "boundary_demo.py": (
                "class Token:\n def __init__(self, value): self.value = value\ndef make(value): return Token(value)\n"
            )
        },
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    legacy = depfix.import_module(file_spec(legacy_wheel), module="boundary_demo")
    current = depfix.import_module(file_spec(current_wheel), module="boundary_demo")
    legacy_token = legacy.Token("legacy")
    current_token = current.Token("current")

    legacy_info = depfix.realm_of(legacy_token)
    current_info = depfix.realm_of(current)
    assert legacy_info is not None and current_info is not None
    assert legacy_info.package == "boundary-demo==1.0.0"
    assert current_info.package == "boundary-demo==2.0.0"
    assert legacy_info.realm_id != current_info.realm_id
    assert depfix.realm_of(legacy.Token) == legacy_info
    assert depfix.realm_of(legacy.make) == legacy_info
    assert depfix.realm_of(1) is None

    depfix.assert_same_realm(current, current_token, {"nested": [current_token]}, object())
    depfix.assert_same_realm(current_info, current_token)
    with pytest.raises(depfix.RealmBoundaryError) as captured:
        depfix.assert_same_realm(current, {"nested": [legacy_token]})
    error = captured.value
    assert isinstance(error, TypeError)
    assert error.consumer == "boundary-demo==2.0.0 (boundary_demo)"
    assert error.producer == "boundary-demo==1.0.0 (boundary_demo)"
    assert error.consumer_realm == current_info.realm_id
    assert error.producer_realm == legacy_info.realm_id
    assert error.value_path == "values[0].values[0][0]"
    assert "application-owned primitive representation" in str(error)

    @depfix.enforce_same_realm(current, parameters=("token",))
    def consume(prefix: object, token: object) -> str:
        return f"{prefix}:{token.value}"  # type: ignore[attr-defined]

    assert consume("ok", current_token) == "ok:current"
    with pytest.raises(depfix.RealmBoundaryError, match=r"parameter\['token'\]"):
        consume("blocked", legacy_token)

    @depfix.enforce_same_realm(current, check_return=True)
    def leak() -> object:
        return legacy_token

    with pytest.raises(depfix.RealmBoundaryError, match="return"):
        leak()

    @depfix.enforce_same_realm(current, parameters="token")
    async def consume_async(token: object) -> str:
        return token.value  # type: ignore[attr-defined]

    assert inspect.iscoroutinefunction(consume_async)
    assert asyncio.run(consume_async(current_token)) == "current"
    with pytest.raises(depfix.RealmBoundaryError):
        asyncio.run(consume_async(legacy_token))

    depfix.assert_same_realm(current, [legacy_token], recursive=False)
    with pytest.raises(depfix.RealmBoundaryError):
        depfix.assert_same_realm(current, [legacy_token])
    with pytest.raises(depfix.RealmBoundaryError, match="no Depfix realm provenance"):
        depfix.assert_same_realm(object(), current_token)
    with pytest.raises(ValueError, match="missing"):

        @depfix.enforce_same_realm(current, parameters=("missing",))
        def invalid(value: object) -> object:
            return value


def test_auto_uses_process_shared_imports_for_native_graphs_and_rejects_a_second_version(
    tmp_path: Path, wheel_factory
) -> None:
    first = wheel_factory(
        "shared-native-demo",
        "1.0.0",
        {"shared_native_demo.py": "VERSION = 'one'\n", "shared_native_accelerator.so": b"native marker"},
    )
    second = wheel_factory(
        "shared-native-demo",
        "2.0.0",
        {"shared_native_demo.py": "VERSION = 'two'\n", "shared_native_accelerator.so": b"native marker"},
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    loaded = depfix.import_module(file_spec(first), module="shared_native_demo")
    assert loaded.VERSION == "one"
    assert loaded.__name__ == "shared_native_demo"
    assert loaded.__depfix_distribution__ == "shared-native-demo"
    assert loaded.__depfix_version__ == "1.0.0"
    assert sys.modules["shared_native_demo"] is loaded
    assert depfix.import_module(file_spec(first), module="shared_native_demo") is loaded

    with pytest.raises(SharedImportConflictError, match="cannot replace") as captured:
        depfix.import_module(file_spec(second), module="shared_native_demo")
    assert "shared-native-demo==1.0.0" in captured.value.candidates[0]
    assert "shared-native-demo==2.0.0" in captured.value.candidates[0]


def test_explicit_inprocess_mode_keeps_native_marked_python_roots_isolated(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "strict-native-demo",
        "1.0.0",
        {"strict_native_demo.py": "VALUE = 1\n", "strict_native_accelerator.so": b"native marker"},
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    loaded = depfix.import_module(file_spec(wheel), module="strict_native_demo", isolation="inprocess")

    assert loaded.VALUE == 1
    assert loaded.__name__.startswith("_depfix.")
    assert "strict_native_demo" not in sys.modules


def test_allow_unsafe_enables_deliberate_inprocess_extension_loading(tmp_path: Path, wheel_factory) -> None:
    extension = importlib.util.find_spec("_testcapi")
    if extension is None or extension.origin is None or not Path(extension.origin).is_file():
        pytest.skip("this interpreter does not provide the _testcapi extension")
    source = Path(extension.origin)
    wheel = wheel_factory(
        "unsafe-native-probe",
        "1.0.0",
        {source.name: source.read_bytes()},
        metadata_version="2.5",
        import_names=("_testcapi",),
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    with pytest.raises(depfix.NativeIsolationRequired) as captured:
        depfix.import_module(file_spec(wheel), module="_testcapi", isolation="inprocess")
    message = str(captured.value)
    assert "allow_unsafe=True" in message
    assert "depfix.configure(allow_unsafe=True)" in message

    loaded = depfix.import_module(file_spec(wheel), module="_testcapi", isolation="inprocess", allow_unsafe=True)
    assert loaded.__name__.startswith("_depfix.")
    assert loaded.__depfix_logical_name__ == "_testcapi"
    assert callable(loaded.parse_tuple_and_keywords)


def test_known_unsafe_classification_requires_an_explicit_or_global_override(
    tmp_path: Path,
    wheel_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from depfix import resolver as resolver_module

    wheel = wheel_factory("known-unsafe-demo", "1.0.0", {"known_unsafe_demo.py": "VALUE = 1\n"})
    original_inspect = resolver_module.inspect_wheel

    def classify_as_unsafe(path: Path, **kwargs):  # type: ignore[no-untyped-def]
        return replace(original_inspect(path, **kwargs), native_classification="native-known-unsafe")

    monkeypatch.setattr(resolver_module, "inspect_wheel", classify_as_unsafe)
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    with pytest.raises(UnsafePackageError) as captured:
        depfix.import_module(file_spec(wheel), module="known_unsafe_demo")
    assert captured.value.candidates == ("known-unsafe-demo==1.0.0",)
    message = str(captured.value)
    assert "allow_unsafe=True" in message
    assert "DEPFIX_ALLOW_UNSAFE=1" in message

    assert depfix.import_module(file_spec(wheel), module="known_unsafe_demo", allow_unsafe=True).VALUE == 1

    depfix.configure(allow_unsafe=True)
    assert depfix.import_module(file_spec(wheel), module="known_unsafe_demo").VALUE == 1
    with pytest.raises(UnsafePackageError):
        depfix.import_module(file_spec(wheel), module="known_unsafe_demo", allow_unsafe=False)

    reset_runtime_state()
    prepared_cache = Cache(tmp_path / "prepared-cache")
    prepared = Resolver(prepared_cache).resolve(
        ProjectConfig(
            tmp_path / ".depfix" / "config.toml",
            (
                ImportDeclaration("blocked_alias", file_spec(wheel), "known_unsafe_demo", allow_unsafe=False),
                ImportDeclaration("permitted_alias", file_spec(wheel), "known_unsafe_demo", allow_unsafe=True),
            ),
            {},
        )
    )
    sync_graph(prepared, prepared_cache, offline=True)
    manifest = tmp_path / ".depfix" / "imports.lock"
    write_manifest(prepared, manifest)
    activate_manifest(manifest, Settings(cache_dir=tmp_path / "prepared-cache"))
    blocked = prepared.alias_index["blocked_alias"]
    with pytest.raises(UnsafePackageError):
        load_generated_alias(
            blocked.name,
            (prepared.graph_id, blocked.node, blocked.module, blocked.specifier),
        )
    permitted = prepared.alias_index["permitted_alias"]
    assert (
        load_generated_alias(
            permitted.name,
            (prepared.graph_id, permitted.node, permitted.module, permitted.specifier),
        ).VALUE
        == 1
    )


def test_shared_mode_rejects_a_preloaded_module_without_a_trustworthy_location(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "preloaded-native-demo",
        "1.0.0",
        {"preloaded_native_demo.py": "VALUE = 'locked'\n", "preloaded_native_accelerator.so": b"native marker"},
    )
    ambient = ModuleType("preloaded_native_demo")
    sys.modules[ambient.__name__] = ambient
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    try:
        with pytest.raises(SharedImportConflictError, match="cannot replace") as captured:
            depfix.import_module(file_spec(wheel), module=ambient.__name__)
    finally:
        sys.modules.pop(ambient.__name__, None)

    assert "unknown location" in captured.value.candidates[0]


def test_shared_mode_tolerates_preloaded_private_helpers_for_public_requests(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "private-helper-demo",
        "1.0.0",
        {
            "public_native_demo.py": "VALUE = 'locked'\n",
            "private_native_helper.py": "VALUE = 'private'\n",
            "private_native_accelerator.so": b"native marker",
        },
        metadata_version="2.5",
        import_names=("public_native_demo", "private_native_helper; private"),
    )
    ambient = ModuleType("private_native_helper")
    sys.modules[ambient.__name__] = ambient
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    try:
        loaded = depfix.import_module(file_spec(wheel), module="public_native_demo")
        with pytest.raises(SharedImportConflictError, match="cannot replace"):
            depfix.import_module(file_spec(wheel), module="private_native_helper")
    finally:
        sys.modules.pop(ambient.__name__, None)

    assert loaded.VALUE == "locked"


def test_shared_mode_tolerates_compatibility_alias_submodules(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "compatibility-alias-demo",
        "1.0.0",
        {"compatibility_alias_demo.py": "VALUE = 'locked'\n", "compatibility_alias_accelerator.so": b"native marker"},
    )
    alias = "compatibility_alias_demo.packages.json"
    sys.modules[alias] = json
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    try:
        loaded = depfix.import_module(file_spec(wheel), module="compatibility_alias_demo")
    finally:
        sys.modules.pop(alias, None)

    assert loaded.VALUE == "locked"


def test_shared_mode_restores_a_preexisting_verified_target_path(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "shared-path-demo",
        "1.0.0",
        {"shared_path_demo.py": "VALUE = 1\n", "shared_path_accelerator.so": b"native marker"},
    )
    cache = Cache(tmp_path / "cache")
    graph = Resolver(cache).resolve(
        ProjectConfig(
            tmp_path / ".depfix" / "config.toml",
            (ImportDeclaration("demo", file_spec(wheel), "shared_path_demo"),),
            {},
        )
    )
    sync_graph(graph, cache, offline=True)
    target_path = str(cache.unpacked_path(graph.nodes[0].artifact) / "purelib")
    sys.path.insert(2, target_path)
    before = list(sys.path)
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    try:
        assert depfix.import_module(file_spec(wheel), module="shared_path_demo").VALUE == 1
        assert sys.path[0] == target_path
        reset_runtime_state()
        assert sys.path == before
    finally:
        while target_path in sys.path:
            sys.path.remove(target_path)


def test_auto_mode_is_scoped_to_each_request_in_a_mixed_prepared_manifest(tmp_path: Path, wheel_factory) -> None:
    first = wheel_factory("prepared-old", "1.0.0", {"prepared_target.py": "VERSION = 'old'\n"})
    second = wheel_factory("prepared-new", "2.0.0", {"prepared_target.py": "VERSION = 'new'\n"})
    native = wheel_factory(
        "prepared-native",
        "1.0.0",
        {"prepared_native.py": "VALUE = 'shared'\n", "prepared_native_accelerator.so": b"native marker"},
    )
    graph = Resolver(Cache(tmp_path / "cache")).resolve(
        ProjectConfig(
            tmp_path / ".depfix" / "config.toml",
            (
                ImportDeclaration("old", file_spec(first), "prepared_target"),
                ImportDeclaration("new", file_spec(second), "prepared_target"),
                ImportDeclaration("native", file_spec(native), "prepared_native"),
            ),
            {},
        )
    )
    manifest = tmp_path / ".depfix" / "imports.lock"
    cache = Cache(tmp_path / "cache")
    write_manifest(graph, manifest)
    sync_graph(graph, cache, offline=True)
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    with pytest.warns(DeprecationWarning, match=r"depfix\.activate\(\) is deprecated"):
        activated = depfix.activate(manifest, cache_dir=tmp_path / "cache")
    activated_old = activated.load_alias("old")  # type: ignore[attr-defined]
    activated_new = activated.load_alias("new")  # type: ignore[attr-defined]
    activated_native = activated.load_alias("native")  # type: ignore[attr-defined]
    assert activated_old.VERSION == "old" and activated_new.VERSION == "new"
    assert activated_old is not activated_new
    assert activated_native.VALUE == "shared" and activated_native.__name__ == "prepared_native"

    old = depfix.import_module(file_spec(first), module="prepared_target", manifest=manifest, frozen=True, offline=True)
    new = depfix.import_module(
        file_spec(second), module="prepared_target", manifest=manifest, frozen=True, offline=True
    )
    shared = depfix.import_module(
        file_spec(native), module="prepared_native", manifest=manifest, frozen=True, offline=True
    )

    assert old.VERSION == "old" and new.VERSION == "new" and old is not new
    assert old.__name__.startswith("_depfix.") and new.__name__.startswith("_depfix.")
    assert shared.VALUE == "shared" and shared.__name__ == "prepared_native"


def test_setuptools_distutils_compatibility_alias_is_package_local(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory(
        "setuptools",
        "75.0.0",
        {
            "setuptools/__init__.py": (
                "import importlib\n"
                "from importlib.machinery import EXTENSION_SUFFIXES\n"
                "import distutils as direct_distutils\n"
                "bundled_distutils = importlib.import_module('distutils')\n"
                "bundled_helper = importlib.import_module('jaraco.functools')\n"
                "class LocalClass: pass\n"
            ),
            "setuptools/_distutils/__init__.py": "VALUE = 'local-distutils'\nclass BaseClass: pass\n",
            "setuptools/_vendor/jaraco/functools.py": "VALUE = 'vendored-helper'\n",
            "pkg_resources/__init__.py": "VALUE = 'resources'\n",
        },
    )
    depfix.configure(cache_dir=tmp_path / "cache")

    setuptools = depfix.import_module(file_spec(wheel), module="setuptools")
    pkg_resources = depfix.import_module(file_spec(wheel), module="pkg_resources")

    assert setuptools.bundled_distutils.VALUE == "local-distutils"
    assert setuptools.direct_distutils is setuptools.bundled_distutils
    assert setuptools.bundled_helper.VALUE == "vendored-helper"
    assert isinstance(setuptools.EXTENSION_SUFFIXES, list)
    assert str(setuptools.LocalClass.__module__).startswith("_depfix.")
    assert setuptools.LocalClass.__module__.startswith("setuptools")
    assert setuptools.bundled_distutils.BaseClass.__module__.startswith("distutils")
    assert pkg_resources.VALUE == "resources"
    assert "distutils" not in sys.modules


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
                "Index https://user:password@example.test/simple\n"  # pragma: allowlist secret
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


def test_cli_prefer_newest_option_is_available_after_the_command(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wheel = wheel_factory("newest-cli-demo", "1.0.0", {"newest_cli_demo.py": "VALUE = 1\n"})

    result = cli_main(
        [
            "fetch",
            file_spec(wheel),
            "--prefer-newest",
            "--json",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["version"] == "1.0.0"
    assert resolve_settings(discover=False).prefer_newest is True


def test_install_cli_exposes_only_effective_artifact_options() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "depfix", "install", "--help"],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "--no-build" in result.stdout
    for removed in ("--allow-build", "--only-binary", "--index-url", "--extra-index-url", "--refresh"):
        assert removed not in result.stdout


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
        '[settings]\nallow-unsafe = true\n[resolver]\nindex-url = "https://config.example/simple"\n'
        "offline = true\nprefer-newest = true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    configured = resolve_settings()
    assert configured.index_url == "https://config.example/simple"
    assert configured.offline is True
    assert configured.allow_unsafe is True
    assert configured.prefer_newest is True

    monkeypatch.setenv("DEPFIX_INDEX_URL", "https://env.example/simple")
    monkeypatch.setenv("DEPFIX_OFFLINE", "0")
    monkeypatch.setenv("DEPFIX_ALLOW_UNSAFE", "0")
    monkeypatch.setenv("DEPFIX_PREFER_NEWEST", "0")
    environment = resolve_settings()
    assert environment.index_url == "https://env.example/simple"
    assert environment.offline is False
    assert environment.allow_unsafe is False
    assert environment.prefer_newest is False

    depfix.configure(index_url="https://python.example/simple", offline=True, allow_unsafe=True, prefer_newest=True)
    assert resolve_settings().index_url == "https://python.example/simple"
    assert resolve_settings().allow_unsafe is True
    assert resolve_settings().prefer_newest is True
    explicit = resolve_settings(
        index_url="https://call.example/simple",
        offline=False,
        allow_unsafe=False,
        prefer_newest=False,
    )
    assert explicit.index_url == "https://call.example/simple"
    assert explicit.offline is False
    assert explicit.allow_unsafe is False
    assert explicit.prefer_newest is False


def test_every_loading_api_exposes_the_per_request_unsafe_override() -> None:
    for api in (
        depfix.import_module,
        depfix.load_package,
        depfix.import_module_async,
        depfix.load_package_async,
        depfix.default,
        depfix.using,
    ):
        parameter = inspect.signature(api).parameters["allow_unsafe"]
        assert parameter.default is None
        newest_parameter = inspect.signature(api).parameters["prefer_newest"]
        assert newest_parameter.default is None
        assert inspect.signature(api).parameters["index_url"].default is None
        assert inspect.signature(api).parameters["extra_index_url"].default is None


def test_scanner_is_static_and_reports_dynamic_calls(tmp_path: Path) -> None:
    source = tmp_path / "application.py"
    source.write_text(
        "from depfix import import_module as versioned_import\n"
        'SPEC = "idna" + "==3.10"\n'
        "safe = versioned_import(SPEC, isolation='shared', allow_unsafe=True, prefer_newest=True, "
        "index_url='https://download.example/simple')\n"
        "unsafe = versioned_import(read_spec())\n",
        encoding="utf-8",
    )
    result = scan_project(tmp_path)
    assert len(result.requests) == 1
    assert result.requests[0].normalized_specifier == "idna==3.10"
    assert result.requests[0].assignment == "safe"
    assert result.requests[0].isolation == "shared"
    assert result.requests[0].allow_unsafe is True
    assert result.requests[0].prefer_newest is True
    assert result.requests[0].index_url == "https://download.example/simple"
    assert result.requests[0].extra_index_url is None
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
    with pytest.raises(ValueError, match="target requires local=True"):
        install_manifest(manifest, target=tmp_path / "invalid-target", cache_dir=cache_dir)
    vendored = tmp_path / "vendored"
    local = install_manifest(manifest, local=True, target=vendored, offline=True, cache_dir=cache_dir)
    assert local.target == vendored
    assert (vendored / graph.artifacts[0].sha256[:16] / "purelib" / "bundle_demo" / "__init__.py").is_file()
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
    candidates = {
        "Depfix runtime dependency": (Path(sys.executable).absolute().parent / executable.path.name).resolve(),
        "current Python scripts directory": (Path(sysconfig.get_path("scripts")) / executable.path.name).resolve(),
    }
    assert executable.source in candidates
    assert executable.path == candidates[executable.source]


def test_depfix_pip_version_reports_the_uv_backend() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "depfix", "pip", "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("uv ")


def test_depfix_pip_install_populates_store_with_conflicting_dependency_realms(
    tmp_path: Path,
    wheel_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dependency_v1 = wheel_factory("pip-shared", "1.0.0", {"pip_shared.py": "VERSION = 1\n"})
    dependency_v2 = wheel_factory("pip-shared", "2.0.0", {"pip_shared.py": "VERSION = 2\n"})
    package_a = wheel_factory(
        "pip-package-a",
        "1.0.0",
        {"pip_package_a.py": "VALUE = 'a'\n"},
        requires=["pip-shared<2"],
    )
    package_b = wheel_factory(
        "pip-package-b",
        "1.0.0",
        {"pip_package_b.py": "VALUE = 'b'\n"},
        requires=["pip-shared>=2"],
    )
    index = build_index(tmp_path / "index", [dependency_v1, dependency_v2])
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(f"{file_spec(package_a)}\n{file_spec(package_b)}\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    before_path = list(sys.path)
    assert importlib.util.find_spec("pip_package_a") is None

    result = cli_main(
        [
            "pip",
            "install",
            "-r",
            str(requirements),
            "--index-url",
            index,
            "--cache-dir",
            str(cache_dir),
            "--json",
        ]
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    graph = load_manifest(Path(output["manifest"]))
    shared_versions = {node.version for node in graph.nodes if node.distribution == "pip-shared"}
    assert output["packages"] == ["pip-package-a==1.0.0", "pip-package-b==1.0.0"]
    assert shared_versions == {"1.0.0", "2.0.0"}
    assert {item.distribution for item in Cache(cache_dir).list_packages()} == {
        "pip-package-a",
        "pip-package-b",
        "pip-shared",
    }
    assert sys.path == before_path
    assert importlib.util.find_spec("pip_package_a") is None


def test_depfix_pip_install_applies_nested_requirement_constraints(
    tmp_path: Path,
    wheel_factory,
) -> None:
    dependency_v1 = wheel_factory("constrained-shared", "1.0.0", {"constrained_shared.py": "VERSION = 1\n"})
    dependency_v2 = wheel_factory("constrained-shared", "2.0.0", {"constrained_shared.py": "VERSION = 2\n"})
    package = wheel_factory(
        "constrained-root",
        "1.0.0",
        {"constrained_root.py": "VALUE = 1\n"},
        requires=["constrained-shared>=1"],
    )
    index = build_index(tmp_path / "index", [dependency_v1, dependency_v2])
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("constrained-shared<2\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(f"-c {constraints.name}\n{file_spec(package)}\n", encoding="utf-8")

    installed = install_packages(
        [file_spec(package)],
        constraints=["constrained-shared<2"],
        index_url=index,
        cache_dir=tmp_path / "api-cache",
        base_dir=tmp_path,
    )
    api_graph = load_manifest(installed.manifest)
    assert {node.version for node in api_graph.nodes if node.distribution == "constrained-shared"} == {"1.0.0"}

    result = cli_main(
        [
            "pip",
            "install",
            "-r",
            str(requirements),
            "--index-url",
            index,
            "--cache-dir",
            str(tmp_path / "cli-cache"),
            "--quiet",
        ]
    )
    assert result == 0
    manifests = tuple((Cache(tmp_path / "cli-cache").root / "installs").glob("*/imports.lock"))
    assert len(manifests) == 1
    cli_graph = load_manifest(manifests[0])
    assert cli_graph.policy["constraints"] == ["constrained-shared<2"]
    assert {node.version for node in cli_graph.nodes if node.distribution == "constrained-shared"} == {"1.0.0"}


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
