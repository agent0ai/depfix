# DOX framework

- DOX is highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits
- Depfix is owned by `agent0ai`; public project language should fit the direct, product-led style of Agent Zero, Space Agent, and DOX

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the DOX pass still must happen.

## Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child DOX Index
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if there are no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists

Default section order:
- Purpose
- Ownership
- Local Contracts
- Work Guidance
- Verification
- Child DOX Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the DOX chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child DOX Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why

## User Preferences

- Keep repository concepts in clear folder boundaries so each can carry focused DOX guidance.
- Maintain one polished `README.md` that renders well on both GitHub and PyPI.
- Lead the public README with plain-language importing workflows and developer outcomes; keep internal implementation
  terminology in the deeper documentation.
- Keep package, CLI, and GitHub summary metadata focused on dependency conflicts and multiversion imports; reserve
  internal terms such as realms for technical documentation.
- Open the public README with a compact visual summary of the primary developer wins before detailed API examples.
- Present Depfix as production ready through its CLI and Python APIs for preinstallation and vendoring.
- Treat `agent0ai` as the project owner and use `https://github.com/agent0ai/depfix` as the canonical repository URL.
- License Depfix under MIT with `Agent Zero, s.r.o` copyright ownership and `pr@agent-zero.ai` as the contact.
- Show concise, secret-safe package preparation progress on stderr by default; keep quiet and structured output modes clean.
- Keep `default()` and `using()` as the lowercase public standard-import APIs; ordinary imports must preserve realm identity,
  scope isolation, frozen preparation, and unmanaged Python behavior.
- Let `auto` load pure graphs in isolated realms and native graphs through one process-shared public import owner. Do not
  reject a first compatible native request solely because it is native, including through `using()` syntax sugar; keep
  that native version as the process owner after scope exit and reject incompatible replacement or strict in-process
  native loading instead of overstating isolation.
- Keep `depfix.configure()` as the single process-wide Python configuration entry point. Unsafe loading stays disabled by
  default, is overridable per loading request, and never bypasses integrity, network, or incompatible-owner checks.
- Keep the cross-project package cache observable and bounded: record first installation and successful import use,
  protect returning graphs and active runtimes, run configurable retention work off the import path, and expose matching
  Python and CLI inventory/removal operations.
- Prefer the newest compatible artifact already present in the shared cache during resolution. Keep newest-first selection
  available through one inherited `prefer_newest` setting and per-loading-request overrides; exact prepared graphs remain
  immutable until explicitly refreshed or exported again.
- Keep `depfix pip install` Depfix-native: package arguments and requirement files populate the shared package store as a
  grouped resolution, preserve incompatible transitive versions, and never mutate the active Python environment.
- Publish production PyPI releases only through an explicit manual workflow dispatch from a version-matched annotated tag.
  The complete CI and distribution gate must pass before automation stages a hidden GitHub Release draft, then the
  protected `pypi` environment and OIDC Trusted Publishing may upload those exact artifacts. Make the release public only
  after PyPI verification; never store a PyPI token or publish from a push/tag alone.
- Keep the local `.env` owner-readable only for explicitly authorized publication; never print, package, or commit it or
  persist its credentials elsewhere.
- Keep the shared package store inspectable through equivalent Python and CLI flat, duplicate-footprint, and
  installation-tree views. Retain secret-safe command or source-path/line provenance for successful installations.

## Child DOX Index

- [`.github/AGENTS.md`](.github/AGENTS.md) — GitHub automation and repository-hosted operations.
- [`.vscode/AGENTS.md`](.vscode/AGENTS.md) — shared VS Code debugger configuration.
- [`benchmarks/AGENTS.md`](benchmarks/AGENTS.md) — focused performance and identity probes.
- [`docs/AGENTS.md`](docs/AGENTS.md) — user, operator, architecture, and research documentation.
- [`examples/AGENTS.md`](examples/AGENTS.md) — runnable usage and deployment examples.
- [`schemas/AGENTS.md`](schemas/AGENTS.md) — externally consumable data contracts.
- [`scripts/AGENTS.md`](scripts/AGENTS.md) — release and project-maintenance tooling.
- [`src/AGENTS.md`](src/AGENTS.md) — distributable Python source.
- [`tests/AGENTS.md`](tests/AGENTS.md) — product and regression verification.

Generated directories such as `dist/`, build metadata, caches, virtual environments, and local `.depfix/` state do not receive DOX files. Root-level packaging, governance, release, security, and public README files remain owned here.
DOX files are repository contracts and remain excluded from wheel and source-distribution artifacts.
