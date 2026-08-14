from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from conftest import build_index, file_spec

import depfix
from depfix.dispatcher import dispatcher_installed
from depfix.errors import DefaultImportConflictError, RequirementsFileError, ResolutionError, SourceError
from depfix.manager import reset_runtime_state
from depfix.requirements import read_requirements
from depfix.settings import reset_configuration


@pytest.fixture(autouse=True)
def _clean_runtime():
    reset_configuration()
    reset_runtime_state()
    yield
    reset_configuration()
    reset_runtime_state()


def test_default_requirements_activates_relative_pathlike_group(tmp_path: Path, wheel_factory) -> None:
    first = wheel_factory("requirements-first", "1.0.0", {"requirements_first.py": "VALUE = 1\n"})
    second = wheel_factory("requirements-second", "1.0.0", {"requirements_second.py": "VALUE = 2\n"})
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    nested = inputs / "nested.txt"
    nested.write_text(f"{file_spec(second)}\n", encoding="utf-8")
    requirements = inputs / "requirements.txt"
    content = "# grouped defaults\n\nrequirements-first @ " + "\\" + "\n" + file_spec(first)
    requirements.write_text(content + f"\n-r {nested.name}\n", encoding="utf-8")
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    old_cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        depfix.default_requirements(Path("inputs/requirements.txt"))
    finally:
        os.chdir(old_cwd)

    assert importlib.import_module("requirements_first").VALUE == 1
    assert importlib.import_module("requirements_second").VALUE == 2


def test_default_requirements_accepts_a_file_without_declarations(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("# defaults are intentionally empty\n\n", encoding="utf-8")

    depfix.default_requirements(requirements)

    assert not dispatcher_installed()


def test_default_requirements_applies_constraints_and_markers(tmp_path: Path, wheel_factory) -> None:
    dependency_v1 = wheel_factory("requirements-shared", "1.0.0", {"requirements_shared.py": "VERSION = 1\n"})
    dependency_v2 = wheel_factory("requirements-shared", "2.0.0", {"requirements_shared.py": "VERSION = 2\n"})
    root = wheel_factory(
        "requirements-root",
        "1.0.0",
        {"requirements_root.py": "import requirements_shared\nVERSION = requirements_shared.VERSION\n"},
        requires=["requirements-shared>=1"],
    )
    excluded = wheel_factory("requirements-excluded", "1.0.0", {"requirements_excluded.py": "VALUE = 1\n"})
    index = build_index(tmp_path / "index", [dependency_v1, dependency_v2])
    (tmp_path / "constraints.txt").write_text("requirements-shared<2\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        f"--index-url {index}\n-c constraints.txt\n{file_spec(root)}\n"
        f"requirements-excluded @ {excluded.as_uri()} ; python_version < '1'\n",
        encoding="utf-8",
    )
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    depfix.default_requirements(requirements)

    assert importlib.import_module("requirements_root").VERSION == 1
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("requirements_excluded")


def test_default_requirements_rejects_direct_root_that_violates_constraint(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("constraint-direct", "2.0.0", {"constraint_direct.py": "VERSION = 2\n"})
    (tmp_path / "constraints.txt").write_text("constraint-direct<2\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(f"-c constraints.txt\n{file_spec(wheel)}\n", encoding="utf-8")
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    with pytest.raises(ResolutionError) as captured:
        depfix.default_requirements(requirements)

    assert "does not satisfy the requirements constraint" in str(captured.value)
    assert f"{requirements}:2" in str(captured.value)
    assert not dispatcher_installed()


def test_default_requirements_rejects_named_single_file_that_violates_constraint(tmp_path: Path) -> None:
    single_file = tmp_path / "constrained_single.py"
    single_file.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "constraints.txt").write_text("single-decl<0\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(f"-c constraints.txt\nsingle-decl @ {single_file.as_uri()}\n", encoding="utf-8")
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    with pytest.raises(ResolutionError) as captured:
        depfix.default_requirements(requirements)

    assert "does not satisfy the requirements constraint" in str(captured.value)
    assert "single-decl==0+" in str(captured.value)
    assert f"{requirements}:2" in str(captured.value)
    assert not dispatcher_installed()


def test_default_requirements_resolution_failure_keeps_declaration_context(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    missing = tmp_path / "missing.whl"
    requirements.write_text(f"file:{missing}\n", encoding="utf-8")
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    with pytest.raises(SourceError) as captured:
        depfix.default_requirements(requirements)

    assert "Local source does not exist" in str(captured.value)
    assert f"{requirements}:1" in str(captured.value)
    assert not dispatcher_installed()


def test_default_requirements_is_idempotent_and_reuses_offline(tmp_path: Path, wheel_factory) -> None:
    wheel = wheel_factory("requirements-warm", "1.0.0", {"requirements_warm.py": "VALUE = 1\n"})
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(file_spec(wheel) + "\n", encoding="utf-8")
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")

    depfix.default_requirements(requirements)
    depfix.default_requirements(requirements)
    reset_runtime_state()
    reset_configuration()
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    depfix.default_requirements(requirements, offline=True)

    assert importlib.import_module("requirements_warm").VALUE == 1


def test_requirements_failures_are_contextual_and_do_not_install_dispatcher(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    with pytest.raises(RequirementsFileError, match=r"missing\.txt:1"):
        depfix.default_requirements(missing)
    assert not dispatcher_installed()

    malformed = tmp_path / "requirements.txt"
    malformed.write_text("demo>=1 --trusted-host user:secret@example.invalid\n", encoding="utf-8")
    with pytest.raises(RequirementsFileError) as captured:
        depfix.default_requirements(malformed)
    assert f"{malformed}:1" in str(captured.value)
    assert "secret" not in str(captured.value)
    assert not dispatcher_installed()


def test_requirements_reject_hash_directive_and_include_cycle_with_context(tmp_path: Path) -> None:
    hashed = tmp_path / "hashed.txt"
    hashed.write_text("demo==1 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    with pytest.raises(RequirementsFileError, match="direct URL"):
        read_requirements(hashed)

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("-r second.txt\n", encoding="utf-8")
    second.write_text("-r first.txt\n", encoding="utf-8")
    with pytest.raises(RequirementsFileError, match="recursive requirements include"):
        read_requirements(first)


def test_parser_preserves_pep508_sources_editables_and_indexes(tmp_path: Path) -> None:
    project = tmp_path / "local-project"
    project.mkdir()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "--index-url https://packages.example/simple\n"
        "--extra-index-url https://mirror.example/simple\n"
        "demo[fast]>=1,<2 ; python_version >= '3'\n"
        "archive @ https://files.example/archive.whl#sha256=" + "a" * 64 + "\n"
        "source @ git+https://example.invalid/source.git@" + "b" * 40 + "\n"
        "-e ./local-project\n",
        encoding="utf-8",
    )

    parsed = read_requirements(requirements)

    assert parsed.index_url == "https://packages.example/simple"
    assert parsed.extra_index_urls == ["https://mirror.example/simple"]
    assert parsed.requirements[0] == "demo[fast]>=1,<2 ; python_version >= '3'"
    assert parsed.requirements[1].startswith("archive @ https://files.example/archive.whl#sha256=")
    assert parsed.requirements[2].startswith("source @ git+https://example.invalid/source.git@")
    assert parsed.requirements[3] == f"file:{project.resolve().as_posix()}"


def test_default_requirements_conflict_rolls_back_new_bindings(tmp_path: Path, wheel_factory) -> None:
    old = wheel_factory("requirements-conflict", "1.0.0", {"requirements_conflict.py": "VERSION = 1\n"})
    new = wheel_factory("requirements-conflict", "2.0.0", {"requirements_conflict.py": "VERSION = 2\n"})
    extra = wheel_factory("requirements-extra", "1.0.0", {"requirements_extra.py": "VALUE = 1\n"})
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(f"{file_spec(new)}\n{file_spec(extra)}\n", encoding="utf-8")
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")
    depfix.default(file_spec(old))

    with pytest.raises(DefaultImportConflictError):
        depfix.default_requirements(requirements)

    assert importlib.import_module("requirements_conflict").VERSION == 1
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("requirements_extra")
