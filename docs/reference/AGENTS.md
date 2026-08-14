# Reference

## Purpose

- Define exact public Python and command-line interfaces.

## Ownership

- `api.md` owns Python signatures, return contracts, configuration, and exceptions.
- `cli.md` owns commands, options, and output behavior.

## Local Contracts

- Reference names and defaults must match implementation help and signatures.
- Document `default()`, `default_requirements()`, and `using()` as the standard-import APIs; requirements grammar and
  contextual rejection must match the canonical CLI parser, and deprecated prototype activation is not a preferred API.
- Document `patch_import()` as an explicit installed-store-only fallback, including ordinary precedence, deterministic
  selection, exact configured-manifest priority, ambiguity, process/thread scope, no-network behavior, and reversible hook
  boundaries.
- Document `depfix run` as enabling that installed-store fallback automatically after configuration/preparation for both
  script and module execution.
- Keep `depfix.configure()` documented as the canonical process-wide Python configuration entry point and distinguish
  effective defaults from inheriting per-call `None` values.
- Keep compatible cache-first selection and the inherited `prefer_newest` override aligned across loading signatures,
  project export, environment/project configuration, and CLI help.
- Keep request-scoped index signatures aligned across sync, async, and standard-import APIs; document that a scoped
  primary suppresses inherited extras and that prepared manifests reject inapplicable live index arguments.
- Define `depfix pip install` as grouped shared-store preparation, not a pip/uv environment passthrough, and keep its
  supported requirement-file grammar aligned with `project.install_packages()`.
- Keep installed-store inspection signatures, CLI views, provenance fields, process-local active compatibility field,
  duplicate semantics, sizes, and UTC timestamps aligned across top-level flat package lists and installation dependency
  trees. Keep live-resolution
  records under cache-oriented syntax and manifest inspection explicitly manifest-scoped.
- Keep uninstall CLI/Python signatures, PEP 440 selection rules, dry-run/JSON reporting, active protection, non-cascading
  dependency behavior, and advanced `cache remove` compatibility aligned.
- Keep `RealmInfo`, provenance inspection, exact-realm assertions, boundary decorators, and `RealmBoundaryError`
  signatures aligned with the lazy public exports and documented detection limits.

## Work Guidance

- Prefer precise declarative wording and link to guides for longer workflows.

## Verification

- Compare Python reference with exported call signatures and CLI reference with `depfix --help` plus subcommand help.

## Child DOX Index
