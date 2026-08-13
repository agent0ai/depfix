from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _release_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("depfix_release_tool", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path, *, annotated: bool = True) -> Path:
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "src/depfix").mkdir(parents=True)
    (root / "src/depfix/_version.py").write_text('__version__ = "9.8.7"\n', encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## 9.8.7 - 2026-08-06\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Depfix tests", "-c", "user.email=tests@example.test", "commit", "-m", "release")
    if annotated:
        _git(
            root,
            "-c",
            "user.name=Depfix tests",
            "-c",
            "user.email=tests@example.test",
            "tag",
            "-a",
            "v9.8.7",
            "-m",
            "9.8.7",
        )
    else:
        _git(root, "tag", "v9.8.7")
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "origin", "main", "v9.8.7")
    return root


def test_release_candidate_requires_matching_local_remote_annotated_tag(tmp_path: Path) -> None:
    release = _release_module()
    root = _repository(tmp_path)

    candidate = release.validate_candidate("9.8.7", root=root)

    assert candidate.version == "9.8.7"
    assert candidate.tag == "v9.8.7"
    assert candidate.commit == _git(root, "rev-parse", "HEAD")


def test_release_candidate_rejects_lightweight_tag(tmp_path: Path) -> None:
    release = _release_module()
    root = _repository(tmp_path, annotated=False)

    with pytest.raises(release.ReleaseError, match="must be an annotated tag"):
        release.validate_candidate("9.8.7", root=root)


def test_release_dispatch_payload_can_only_target_validated_tag() -> None:
    release = _release_module()
    candidate = release.ReleaseCandidate(version="9.8.7", tag="v9.8.7", commit="a" * 40)

    payload = json.loads(release.dispatch_payload(candidate, "release-depfix-9.8.7"))

    assert payload == {
        "ref": "v9.8.7",
        "inputs": {"version": "9.8.7", "confirmation": "release-depfix-9.8.7"},
    }
    with pytest.raises(release.ReleaseError, match="confirmation must be"):
        release.dispatch_payload(candidate, "release-depfix-9.8.6")
    with pytest.raises(release.ReleaseError, match="workflow ref must be"):
        release.dispatch_payload(
            release.ReleaseCandidate(version="9.8.7", tag="main", commit="a" * 40),
            "release-depfix-9.8.7",
        )
