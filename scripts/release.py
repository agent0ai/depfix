#!/usr/bin/env python3
"""Validate and safely dispatch a Depfix production release."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "agent0ai/depfix"
WORKFLOW = "publish-pypi.yml"
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class ReleaseError(RuntimeError):
    """A release request failed a safety check."""


@dataclass(frozen=True)
class ReleaseCandidate:
    version: str
    tag: str
    commit: str


def _git(*arguments: str, root: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise ReleaseError(detail)
    return result.stdout.strip()


def _remote_sha(ref: str, *, root: Path = ROOT) -> str:
    output = _git("ls-remote", "--exit-code", "origin", ref, root=root)
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if len(lines) != 1 or len(lines[0]) != 2:
        raise ReleaseError(f"origin did not return exactly one object for {ref}")
    return lines[0][0]


def validate_candidate(version: str, *, root: Path = ROOT) -> ReleaseCandidate:
    """Validate local and remote immutable release identity."""

    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseError("version must be a stable X.Y.Z release")

    tag = f"v{version}"
    source = (root / "src/depfix/_version.py").read_text(encoding="utf-8")
    match = re.fullmatch(r'__version__ = "([^"]+)"\n?', source)
    if match is None or match.group(1) != version:
        raise ReleaseError("requested and package versions do not match")

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = rf"^## {re.escape(version)} - [0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$"
    if re.search(heading, changelog, re.MULTILINE) is None:
        raise ReleaseError(f"CHANGELOG.md has no dated {version} release section")

    if _git("status", "--porcelain", "--untracked-files=normal", root=root):
        raise ReleaseError("release checkout must be clean")

    commit = _git("rev-parse", "HEAD", root=root)
    if _git("rev-parse", "refs/heads/main", root=root) != commit:
        raise ReleaseError("release checkout must be at the local main commit")
    if _git("cat-file", "-t", f"refs/tags/{tag}", root=root) != "tag":
        raise ReleaseError(f"{tag} must be an annotated tag")
    if _git("rev-parse", f"refs/tags/{tag}^{{commit}}", root=root) != commit:
        raise ReleaseError(f"{tag} must point to the checked-out commit")
    if _remote_sha("refs/heads/main", root=root) != commit:
        raise ReleaseError("origin/main does not match the checked-out commit")
    if _remote_sha(f"refs/tags/{tag}^{{}}", root=root) != commit:
        raise ReleaseError(f"origin/{tag} is missing or does not point to the checked-out commit")
    return ReleaseCandidate(version=version, tag=tag, commit=commit)


def _url_status(url: str, *, token: str | None = None) -> int:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "depfix-release-check"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def verify_unpublished(candidate: ReleaseCandidate, *, token: str | None = None) -> None:
    """Require both public release destinations to be unused."""

    github_url = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{candidate.tag}"
    github_status = _url_status(github_url, token=token)
    if github_status != 404:
        if github_status == 200:
            raise ReleaseError(f"GitHub Release {candidate.tag} already exists")
        raise ReleaseError(f"GitHub release lookup returned HTTP {github_status}")

    pypi_url = f"https://pypi.org/pypi/depfix/{candidate.version}/json"
    pypi_status = _url_status(pypi_url)
    if pypi_status != 404:
        if pypi_status == 200:
            raise ReleaseError(f"depfix {candidate.version} already exists on PyPI")
        raise ReleaseError(f"PyPI release lookup returned HTTP {pypi_status}")


def dispatch_payload(candidate: ReleaseCandidate, confirmation: str) -> bytes:
    expected_tag = f"v{candidate.version}"
    if VERSION_PATTERN.fullmatch(candidate.version) is None or candidate.tag != expected_tag:
        raise ReleaseError(f"workflow ref must be the validated {expected_tag} tag")
    expected = f"release-depfix-{candidate.version}"
    if confirmation != expected:
        raise ReleaseError(f"confirmation must be {expected}")
    return json.dumps(
        {
            "ref": candidate.tag,
            "inputs": {"version": candidate.version, "confirmation": confirmation},
        },
        separators=(",", ":"),
    ).encode()


def dispatch(candidate: ReleaseCandidate, confirmation: str, *, token: str) -> None:
    """Dispatch the production workflow against the validated tag."""

    url = f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/dispatches"
    request = urllib.request.Request(
        url,
        data=dispatch_payload(candidate, confirmation),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "depfix-release-dispatch",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 204:
                raise ReleaseError(f"GitHub workflow dispatch returned HTTP {response.status}")
    except urllib.error.HTTPError as error:
        raise ReleaseError(f"GitHub workflow dispatch returned HTTP {error.code}") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="stable version without the leading v")
    parser.add_argument(
        "--dispatch",
        action="store_true",
        help="dispatch publish-pypi.yml after every preflight check passes",
    )
    parser.add_argument(
        "--confirmation",
        help="required with --dispatch: release-depfix-X.Y.Z",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        candidate = validate_candidate(arguments.version)
        verify_unpublished(candidate, token=token)
        print(f"Release candidate ready: {candidate.tag} at {candidate.commit}")
        print("Local/remote main, annotated tag, version, changelog, GitHub, and PyPI checks passed.")
        if arguments.dispatch:
            if not token:
                raise ReleaseError("--dispatch requires GH_TOKEN or GITHUB_TOKEN")
            if not arguments.confirmation:
                raise ReleaseError("--dispatch requires --confirmation")
            dispatch(candidate, arguments.confirmation, token=token)
            print(f"Dispatched {WORKFLOW} from {candidate.tag}.")
        elif arguments.confirmation:
            raise ReleaseError("--confirmation is only valid with --dispatch")
    except (OSError, ReleaseError) as error:
        print(f"release: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
