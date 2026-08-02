# GitHub operations

## Purpose

- Own repository-hosted automation and GitHub presentation assets.

## Ownership

- `REPOSITORY_METADATA.md` records the intended GitHub About settings.
- GitHub Actions configuration, future issue templates, and repository presentation resources belong here.

## Local Contracts

- Workflows use least-privilege permissions and immutable, explicit release gates.
- Production publication requires an explicit manual workflow dispatch from a version-matched annotated tag. Automation
  must run the complete release gate before staging a hidden release draft, then use the protected `pypi` environment and
  OIDC trusted publishing for the exact checked artifacts. The release becomes public only after PyPI verification;
  pushes and tags alone must not publish.
- README presentation assets use plain developer language and emphasize runtime installation and multiversion imports.

## Work Guidance

- Keep hosted automation aligned with the supported Python, platform, architecture, and uv matrices in `pyproject.toml` and the public docs.

## Verification

- Validate workflow YAML and exercise equivalent local commands before changing a gate.

## Child DOX Index

- [`workflows/AGENTS.md`](workflows/AGENTS.md) — CI, TestPyPI, and PyPI workflows.
