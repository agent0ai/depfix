# Core concepts

## Purpose

- Separate Depfix's stable technical ideas into independently understandable and maintainable domains.

## Ownership

- Each child folder owns one concept, its boundaries, and links to deeper reference material.

## Local Contracts

- Concept docs explain why the system is shaped this way; references define exact syntax and guides define tasks.
- A concept folder must remain useful when read independently with the parent documentation index.

## Work Guidance

- Keep concept boundaries narrow and add cross-links where a workflow crosses domains.

## Verification

- Compare concept claims with implementation tests and the current public API.

## Child DOX Index

- [`artifacts-and-cache/AGENTS.md`](artifacts-and-cache/AGENTS.md) — immutable content, verification, and materialization.
- [`deployment/AGENTS.md`](deployment/AGENTS.md) — live, prepared, and air-gapped operation.
- [`import-realms/AGENTS.md`](import-realms/AGENTS.md) — runtime identity and dependency isolation.
- [`manifests/AGENTS.md`](manifests/AGENTS.md) — deterministic resolved graph records.
- [`resolution/AGENTS.md`](resolution/AGENTS.md) — source normalization, selection, inspection, and uv.
