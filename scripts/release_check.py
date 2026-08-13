#!/usr/bin/env python3
"""Build and verify the exact Depfix distributions without publishing them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
FORBIDDEN_PARTS = {
    ".depfix",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".DS_Store",
    "AGENTS.md",
    "__pycache__",
    "tests",
    "tmp",
}
SECRET_PATTERNS = (
    ("private key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}")),
    ("GitHub token", re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("GitLab token", re.compile(rb"glpat-[A-Za-z0-9_-]{20,}")),
    ("Hugging Face token", re.compile(rb"hf_[A-Za-z0-9]{30,}")),
    ("npm token", re.compile(rb"npm_[A-Za-z0-9]{30,}")),
    ("OpenAI token", re.compile(rb"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Anthropic token", re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("PyPI token", re.compile(rb"pypi-[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}")),
    ("Stripe live secret", re.compile(rb"(?:sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("SendGrid token", re.compile(rb"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{20,}")),
    ("Google API key", re.compile(rb"AIza[0-9A-Za-z_-]{30,}")),
    ("credential URL", re.compile(rb"https?://[^/@\s:]+:[^/@\s]+@[^/\s]+")),
)


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> str:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode:
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise SystemExit(result.returncode)
    return result.stdout


def quality_gates() -> None:
    run([sys.executable, "scripts/validate_workflows.py"])
    run([sys.executable, "-m", "ruff", "format", "--check", "."])
    run([sys.executable, "-m", "ruff", "check", "."])
    run([sys.executable, "-m", "mypy", "src/depfix"])
    run([sys.executable, "-m", "pytest", "-q"])


def clean_build_outputs() -> None:
    for path in (BUILD, DIST):
        if path.exists():
            resolved = path.resolve()
            if resolved.parent != ROOT:
                raise RuntimeError(f"refusing to clean unexpected path {resolved}")
            shutil.rmtree(resolved)


def create_clean_environment(path: Path) -> None:
    # uv-managed CPython builds are dynamically linked; copying only their launcher
    # can omit the adjacent runtime library. POSIX virtual environments normally
    # use symlinks and retain that interpreter layout.
    venv.EnvBuilder(with_pip=True, symlinks=os.name != "nt").create(path)


def _secret_types(data: bytes) -> list[str]:
    findings: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(data):
            if label == "credential URL" and match.group(0).endswith(b"@example.test"):
                continue
            findings.append(label)
            break
    return findings


def validate_repository() -> None:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("release validation requires a Git checkout")
    names = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    checked = 0
    for name in names:
        path = ROOT / name
        if not path.exists() and not path.is_symlink():
            continue
        if path.name == ".DS_Store" or path.name.startswith(".env"):
            raise RuntimeError(f"forbidden tracked repository file: {name}")
        data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        findings = _secret_types(data)
        if findings:
            raise RuntimeError(f"possible credential material in tracked file {name}: {', '.join(findings)}")
        checked += 1
    junk = sorted(
        path.relative_to(ROOT) for path in ROOT.rglob(".DS_Store") if ".git" not in path.relative_to(ROOT).parts
    )
    if junk:
        raise RuntimeError(f"remove local OS metadata before release: {[str(path) for path in junk]}")
    print(f"Repository hygiene: {checked} tracked files checked; no credential patterns or OS metadata found")


def archive_members(path: Path) -> Iterable[tuple[str, bytes]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    yield info.filename, archive.read(info)
        return
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.isfile():
                source = archive.extractfile(member)
                if source is not None:
                    yield member.name, source.read()


def validate_archives(artifacts: list[Path]) -> None:
    wheel = next(path for path in artifacts if path.suffix == ".whl")
    names: set[str] = set()
    for artifact in artifacts:
        for name, data in archive_members(artifact):
            parts = Path(name).parts
            if any(part in FORBIDDEN_PARTS for part in parts) or any(part.startswith(".env") for part in parts):
                raise RuntimeError(f"forbidden archive member in {artifact.name}: {name}")
            findings = _secret_types(data)
            if findings:
                raise RuntimeError(f"possible credential material in {artifact.name}: {name}: {', '.join(findings)}")
            if artifact == wheel:
                names.add(name)
    required_suffixes = {
        "depfix/__init__.py",
        "depfix/py.typed",
        "depfix/schemas/depfix-manifest-v1.schema.json",
    }
    missing = {suffix for suffix in required_suffixes if not any(name.endswith(suffix) for name in names)}
    if missing:
        raise RuntimeError(f"wheel is missing required package data: {sorted(missing)}")
    if not any(name.endswith(".dist-info/entry_points.txt") for name in names):
        raise RuntimeError("wheel is missing the depfix console entry point")


def clean_environment_smoke(wheel: Path) -> None:
    temporary = Path(tempfile.mkdtemp(prefix="depfix-release-check-"))
    try:
        environment_root = temporary / "venv"
        create_clean_environment(environment_root)
        scripts = environment_root / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        command = scripts / ("depfix.exe" if os.name == "nt" else "depfix")
        run([str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)], cwd=temporary)
        run([str(python), "-c", "import depfix; print(depfix.__version__)"], cwd=temporary)
        run([str(command), "--help"], cwd=temporary)
        doctor = json.loads(run([str(command), "--json", "doctor"], cwd=temporary))
        if not doctor.get("uv") or not Path(doctor["uv_path"]).is_file():
            raise RuntimeError("clean wheel installation did not provide a working uv backend")

        cache = temporary / "live-cache"
        live_env = {**os.environ, "DEPFIX_CACHE_DIR": str(cache)}
        run(
            [
                str(python),
                "-c",
                'from depfix import import_module; print(import_module("idna==3.10").__depfix_version__)',
            ],
            cwd=temporary,
            env=live_env,
        )

        project = temporary / "project"
        project.mkdir()
        (project / "helper.py").write_text("VALUE = 'prepared-ok'\n", encoding="utf-8")
        (project / "application.py").write_text(
            'from depfix import import_module\nhelper = import_module("file:./helper.py")\nprint(helper.VALUE)\n',
            encoding="utf-8",
        )
        prepared_env = {**os.environ, "DEPFIX_CACHE_DIR": str(temporary / "prepared-cache")}
        manifest = project / ".depfix" / "imports.lock"
        run([str(command), "export", ".", "--output", str(manifest)], cwd=project, env=prepared_env)
        run(
            [str(command), "install", str(manifest), "--frozen", "--offline"],
            cwd=project,
            env=prepared_env,
        )
        offline_env = {
            **prepared_env,
            "DEPFIX_FROZEN": "1",
            "DEPFIX_OFFLINE": "1",
        }
        output = run([str(python), "application.py"], cwd=project, env=offline_env)
        if output.strip() != "prepared-ok":
            raise RuntimeError("prepared/offline clean-wheel smoke test returned unexpected output")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def private_uv_bootstrap_smoke(wheel: Path) -> None:
    wheel = wheel.resolve()
    temporary = Path(tempfile.mkdtemp(prefix="depfix-uv-bootstrap-check-"))
    try:
        environment_root = temporary / "venv"
        create_clean_environment(environment_root)
        scripts = environment_root / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                str(wheel),
                "packaging>=24",
                "pathspec>=0.12",
                "platformdirs>=4",
            ],
            cwd=temporary,
        )
        cache = temporary / "private-cache"
        environment = {**os.environ, "PATH": "", "DEPFIX_CACHE_DIR": str(cache)}
        code = (
            "from pathlib import Path; "
            "from depfix.cache import Cache; "
            "from depfix.settings import Settings; "
            "from depfix.uv_backend import UvBackend; "
            "from packaging.version import Version; "
            f"settings = Settings(cache_dir=Path({str(cache)!r})); "
            "executable = UvBackend(settings, Cache(settings.cache_dir)).ensure_available(); "
            "assert executable.version >= Version('0.11.0'); "
            "assert executable.source == 'Depfix private bootstrap'; "
            "print(executable.path)"
        )
        output = run([str(python), "-c", code], cwd=temporary, env=environment)
        if str(cache / "v1" / "tools" / "uv") not in output:
            raise RuntimeError("uv bootstrap was not installed in the private Depfix tool cache")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def airgap_runtime_smoke(wheel: Path) -> None:
    wheel = wheel.resolve()
    temporary = Path(tempfile.mkdtemp(prefix="depfix-airgap-check-"))
    try:
        project = temporary / "project"
        project.mkdir()
        (project / "helper.py").write_text("VALUE = 'fully-airgapped-ok'\n", encoding="utf-8")
        (project / "application.py").write_text(
            "from depfix import import_module\nhelper = import_module('file:./helper.py')\nprint(helper.VALUE)\n",
            encoding="utf-8",
        )
        connected_cache = temporary / "connected-cache"
        connected_env = {**os.environ, "DEPFIX_CACHE_DIR": str(connected_cache)}
        manifest = project / ".depfix" / "imports.lock"
        bundle = temporary / "application.depfixbundle"
        run(
            [sys.executable, "-m", "depfix", "export", ".", "--output", str(manifest)],
            cwd=project,
            env=connected_env,
        )
        run(
            [
                sys.executable,
                "-m",
                "depfix",
                "bundle",
                str(manifest),
                "--output",
                str(bundle),
                "--include-depfix-runtime",
                "--cache-dir",
                str(connected_cache),
            ],
            cwd=project,
            env=connected_env,
        )

        runtime = temporary / "runtime-wheels"
        runtime.mkdir()
        with zipfile.ZipFile(bundle) as archive:
            metadata = json.loads(archive.read("bundle.json"))
            entries = metadata.get("runtime_wheels", [])
            if not isinstance(entries, list) or not entries:
                raise RuntimeError("air-gap bundle did not contain runtime wheels")
            bundled_depfix = None
            bundled_uv = False
            for item in entries:
                filename = str(item["filename"])
                data = archive.read(f"runtime/wheels/{filename}")
                (runtime / filename).write_bytes(data)
                if filename.startswith("depfix-"):
                    bundled_depfix = hashlib.sha256(data).hexdigest()
                if filename.startswith("uv-"):
                    bundled_uv = True
            if bundled_depfix != hashlib.sha256(wheel.read_bytes()).hexdigest():
                raise RuntimeError("air-gap bundle did not reuse the exact tested Depfix wheel")
            if not bundled_uv:
                raise RuntimeError("air-gap bundle did not include the mandatory uv wheel")

        environment_root = temporary / "venv"
        create_clean_environment(environment_root)
        scripts = environment_root / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        command = scripts / ("depfix.exe" if os.name == "nt" else "depfix")
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                "--find-links",
                str(runtime),
                str(runtime / wheel.name),
            ],
            cwd=temporary,
        )
        offline_cache = temporary / "offline-cache"
        offline_env = {
            **os.environ,
            "DEPFIX_CACHE_DIR": str(offline_cache),
            "DEPFIX_FROZEN": "1",
            "DEPFIX_OFFLINE": "1",
        }
        run(
            [
                str(command),
                "install",
                str(bundle),
                "--offline",
                "--frozen",
                "--cache-dir",
                str(offline_cache),
            ],
            cwd=project,
            env=offline_env,
        )
        output = run([str(python), "application.py"], cwd=project, env=offline_env)
        if output.strip() != "fully-airgapped-ok":
            raise RuntimeError("fully air-gapped bootstrap returned unexpected output")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def full_release_check() -> None:
    clean_build_outputs()
    run([sys.executable, "-m", "build"])
    artifacts = sorted(DIST.iterdir(), key=lambda path: path.name)
    if (
        len(artifacts) != 2
        or not any(path.suffix == ".whl" for path in artifacts)
        or not any(path.name.endswith(".tar.gz") for path in artifacts)
    ):
        raise RuntimeError(f"expected one wheel and one sdist, found {[path.name for path in artifacts]}")
    run([sys.executable, "-m", "twine", "check", *map(str, artifacts)])
    validate_archives(artifacts)
    wheel = next(path for path in artifacts if path.suffix == ".whl")
    clean_environment_smoke(wheel)
    private_uv_bootstrap_smoke(wheel)
    airgap_runtime_smoke(wheel)
    print("\nArtifacts ready for owner review and manual publication:")
    for path in artifacts:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"  {path.relative_to(ROOT)}  sha256:{digest}")
    print("No upload was attempted.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick", action="store_true", help="run code-quality gates without building/network smoke tests"
    )
    arguments = parser.parse_args()
    validate_repository()
    quality_gates()
    if not arguments.quick:
        full_release_check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
