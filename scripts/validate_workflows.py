#!/usr/bin/env python3
"""Validate syntax and safety contracts for Depfix GitHub Actions workflows."""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


class WorkflowError(RuntimeError):
    """A workflow does not satisfy the repository release contract."""


class UniqueBaseLoader(yaml.BaseLoader):
    """Load GitHub-style scalar values while rejecting shadowed YAML keys."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise WorkflowError(f"duplicate YAML key: {key}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowError(f"{context} must be a mapping")
    return value


def _load(path: Path) -> Mapping[str, Any]:
    # BaseLoader follows GitHub's YAML interpretation for the `on` key instead
    # of converting YAML 1.1 words such as `on` and `off` into booleans.
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueBaseLoader)
    except yaml.YAMLError as error:
        raise WorkflowError(f"{path.relative_to(ROOT)}: invalid YAML: {error}") from error
    return _mapping(data, str(path.relative_to(ROOT)))


def _steps(jobs: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for job_name, raw_job in jobs.items():
        job = _mapping(raw_job, f"job {job_name}")
        raw_steps = job.get("steps", [])
        if not isinstance(raw_steps, list):
            raise WorkflowError(f"job {job_name}.steps must be a list")
        for index, raw_step in enumerate(raw_steps):
            yield _mapping(raw_step, f"job {job_name}.steps[{index}]")


def _permissions(value: Any, context: str) -> dict[str, str]:
    return {str(key): str(item) for key, item in _mapping(value, context).items()}


def _names(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value}
    return {str(value)}


def _validate_action_refs(path: Path, jobs: Mapping[str, Any]) -> None:
    for step in _steps(jobs):
        reference = step.get("uses")
        if reference is None or str(reference).startswith("./"):
            continue
        reference = str(reference)
        if "@" not in reference:
            raise WorkflowError(f"{path.name}: action reference has no version: {reference}")
        revision = reference.rsplit("@", 1)[1]
        if revision in {"main", "master", "latest"}:
            raise WorkflowError(f"{path.name}: mutable action reference is forbidden: {reference}")


def _validate_manual_only(path: Path, workflow: Mapping[str, Any]) -> Mapping[str, Any]:
    triggers = _mapping(workflow.get("on"), f"{path.name}.on")
    if set(map(str, triggers)) != {"workflow_dispatch"}:
        raise WorkflowError(f"{path.name}: release workflow must only allow workflow_dispatch")
    return triggers


def _validate_production(workflow: Mapping[str, Any]) -> None:
    path = WORKFLOWS / "publish-pypi.yml"
    triggers = _validate_manual_only(path, workflow)
    dispatch = _mapping(triggers["workflow_dispatch"], f"{path.name}.workflow_dispatch")
    inputs = _mapping(dispatch.get("inputs"), f"{path.name}.inputs")
    if set(map(str, inputs)) != {"version", "confirmation"}:
        raise WorkflowError("publish-pypi.yml: version and confirmation are the only allowed inputs")
    if _permissions(workflow.get("permissions"), path.name) != {"contents": "read"}:
        raise WorkflowError("publish-pypi.yml: top-level permissions must be contents: read")

    jobs = _mapping(workflow.get("jobs"), f"{path.name}.jobs")
    expected_jobs = {
        "validate-request",
        "checks",
        "stage-release",
        "publish",
        "verify-publication",
        "finalize-release",
        "cleanup-release-draft",
    }
    if set(map(str, jobs)) != expected_jobs:
        raise WorkflowError("publish-pypi.yml: production job graph changed; review the validator contract")

    required_needs = {
        "checks": {"validate-request"},
        "stage-release": {"validate-request", "checks"},
        "publish": {"stage-release"},
        "verify-publication": {"publish"},
        "finalize-release": {"stage-release", "verify-publication"},
        "cleanup-release-draft": {"stage-release", "publish", "verify-publication"},
    }
    for name, expected in required_needs.items():
        job = _mapping(jobs[name], f"publish-pypi.yml.{name}")
        if _names(job.get("needs")) != expected:
            raise WorkflowError(f"publish-pypi.yml: {name} must depend on {sorted(expected)}")
    checks = _mapping(jobs["checks"], "publish-pypi.yml.checks")
    if checks.get("uses") != "./.github/workflows/ci.yml":
        raise WorkflowError("publish-pypi.yml: release checks must call the complete reusable CI workflow")

    publish = _mapping(jobs["publish"], "publish-pypi.yml.publish")
    if _permissions(publish.get("permissions"), "publish permissions") != {"id-token": "write"}:
        raise WorkflowError("publish-pypi.yml: only the publish job may receive id-token: write")
    environment = _mapping(publish.get("environment"), "publish environment")
    if environment.get("name") != "pypi":
        raise WorkflowError("publish-pypi.yml: publish job must use the protected pypi environment")

    writable = {"stage-release", "finalize-release", "cleanup-release-draft"}
    for name, raw_job in jobs.items():
        job = _mapping(raw_job, f"publish-pypi.yml.{name}")
        permissions = _permissions(job["permissions"], f"{name}.permissions") if "permissions" in job else {}
        if "id-token" in permissions and name != "publish":
            raise WorkflowError(f"publish-pypi.yml: {name} must not receive an OIDC token")
        if permissions.get("contents") == "write" and name not in writable:
            raise WorkflowError(f"publish-pypi.yml: {name} must not receive contents: write")
        if name in writable and permissions != {"contents": "write"}:
            raise WorkflowError(f"publish-pypi.yml: {name} must have only contents: write")

    cleanup = _mapping(jobs["cleanup-release-draft"], "cleanup job")
    condition = str(cleanup.get("if", ""))
    if "needs.publish.result != 'success'" not in condition:
        raise WorkflowError("publish-pypi.yml: cleanup must preserve drafts after a successful PyPI upload")
    _validate_action_refs(path, jobs)


def _validate_testpypi(workflow: Mapping[str, Any]) -> None:
    path = WORKFLOWS / "publish-testpypi.yml"
    _validate_manual_only(path, workflow)
    if _permissions(workflow.get("permissions"), path.name) != {"contents": "read"}:
        raise WorkflowError("publish-testpypi.yml: top-level permissions must be contents: read")
    jobs = _mapping(workflow.get("jobs"), f"{path.name}.jobs")
    if set(map(str, jobs)) != {"build-and-test", "publish"}:
        raise WorkflowError("publish-testpypi.yml: staging job graph changed; review the validator contract")
    publish = _mapping(jobs["publish"], "publish-testpypi.yml.publish")
    if _names(publish.get("needs")) != {"build-and-test"}:
        raise WorkflowError("publish-testpypi.yml: publishing must depend on build-and-test")
    if _permissions(publish.get("permissions"), "TestPyPI permissions") != {"id-token": "write"}:
        raise WorkflowError("publish-testpypi.yml: publish job needs only id-token: write")
    environment = _mapping(publish.get("environment"), "TestPyPI environment")
    if environment.get("name") != "testpypi":
        raise WorkflowError("publish-testpypi.yml: publish job must use the testpypi environment")
    build = _mapping(jobs["build-and-test"], "publish-testpypi.yml.build-and-test")
    if "permissions" in build:
        raise WorkflowError("publish-testpypi.yml: build-and-test must inherit read-only permissions")
    _validate_action_refs(path, jobs)


def _validate_recovery(workflow: Mapping[str, Any]) -> None:
    path = WORKFLOWS / "recover-pypi-release.yml"
    triggers = _validate_manual_only(path, workflow)
    dispatch = _mapping(triggers["workflow_dispatch"], f"{path.name}.workflow_dispatch")
    inputs = _mapping(dispatch.get("inputs"), f"{path.name}.inputs")
    if set(map(str, inputs)) != {"version", "confirmation"}:
        raise WorkflowError("recover-pypi-release.yml: version and confirmation are the only allowed inputs")
    if _permissions(workflow.get("permissions"), path.name) != {"contents": "read"}:
        raise WorkflowError("recover-pypi-release.yml: top-level permissions must be contents: read")
    jobs = _mapping(workflow.get("jobs"), f"{path.name}.jobs")
    if set(map(str, jobs)) != {"recover-release"}:
        raise WorkflowError("recover-pypi-release.yml: recovery must use one explicit job")
    job = _mapping(jobs["recover-release"], "recover-pypi-release.yml.recover-release")
    if _permissions(job.get("permissions"), "recovery permissions") != {"contents": "write"}:
        raise WorkflowError("recover-pypi-release.yml: recovery needs only contents: write")
    text = (WORKFLOWS / "recover-pypi-release.yml").read_text(encoding="utf-8")
    required = (
        "recover-depfix-{version}",
        "refs/tags/{tag}",
        "--no-cache-dir",
        "gh release download",
        'gh release edit "v${VERSION}" --draft=false',
    )
    for marker in required:
        if marker not in text:
            raise WorkflowError(f"recover-pypi-release.yml: missing safety marker {marker!r}")
    if "id-token" in text or "gh-action-pypi-publish" in text:
        raise WorkflowError("recover-pypi-release.yml: recovery must never receive OIDC or upload to PyPI")
    _validate_action_refs(path, jobs)


def _validate_ci(workflow: Mapping[str, Any]) -> None:
    path = WORKFLOWS / "ci.yml"
    triggers = _mapping(workflow.get("on"), "ci.yml.on")
    expected = {"push", "pull_request", "workflow_dispatch", "workflow_call"}
    if set(map(str, triggers)) != expected:
        raise WorkflowError("ci.yml: expected push, pull_request, workflow_dispatch, and workflow_call triggers")
    if _permissions(workflow.get("permissions"), path.name) != {"contents": "read"}:
        raise WorkflowError("ci.yml: top-level permissions must be contents: read")
    jobs = _mapping(workflow.get("jobs"), "ci.yml.jobs")
    quality = _mapping(jobs.get("quality"), "ci.yml.quality")
    commands = "\n".join(str(step.get("run", "")) for step in _steps({"quality": quality}))
    if "python scripts/validate_workflows.py" not in commands:
        raise WorkflowError("ci.yml: quality job must validate workflow contracts")
    _validate_action_refs(path, jobs)


def validate() -> None:
    paths = sorted(WORKFLOWS.glob("*.yml"))
    if not paths:
        raise WorkflowError("no GitHub Actions workflows found")
    loaded = {path.name: _load(path) for path in paths}
    required = {"ci.yml", "publish-pypi.yml", "publish-testpypi.yml", "recover-pypi-release.yml"}
    missing = required - loaded.keys()
    if missing:
        raise WorkflowError(f"missing required workflows: {sorted(missing)}")
    for path in paths:
        jobs = _mapping(loaded[path.name].get("jobs"), f"{path.name}.jobs")
        _validate_action_refs(path, jobs)
    _validate_ci(loaded["ci.yml"])
    _validate_production(loaded["publish-pypi.yml"])
    _validate_testpypi(loaded["publish-testpypi.yml"])
    _validate_recovery(loaded["recover-pypi-release.yml"])


def main() -> int:
    try:
        validate()
    except (OSError, WorkflowError) as error:
        print(f"workflow validation: {error}", file=sys.stderr)
        return 2
    print("Workflow validation: YAML and release safety contracts passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
