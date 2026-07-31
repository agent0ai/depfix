# GitHub operations

## Purpose

- Own repository-hosted automation and GitHub presentation assets.

## Ownership

- `REPOSITORY_METADATA.md` records the intended GitHub About settings.
- GitHub Actions configuration, future issue templates, and repository presentation resources belong here.

## Local Contracts

- Workflows use least-privilege permissions and immutable, explicit release gates.
- Publication remains manual and uses trusted publishing; CI must not publish implicitly.
- README presentation assets use plain developer language and emphasize runtime installation and multiversion imports.

## Work Guidance

- Keep hosted automation aligned with the supported Python, platform, architecture, and uv matrices in `pyproject.toml` and the public docs.

## Verification

- Validate workflow YAML and exercise equivalent local commands before changing a gate.

## Child DOX Index

- [`workflows/AGENTS.md`](workflows/AGENTS.md) — CI, TestPyPI, and PyPI workflows.
