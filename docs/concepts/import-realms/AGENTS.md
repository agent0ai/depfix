# Import realms

## Purpose

- Document Depfix's isolated pure-Python realms and guarded process-shared native import model.

## Ownership

- `README.md` owns runtime architecture; `compatibility.md` owns supported and unsupported import behavior, including
  cross-version object boundaries.

## Local Contracts

- Logical module spelling, canonical module identity, artifact identity, node identity, and realm identity stay distinct.
- Realm code resolves third-party imports only through declared parent-specific edges.
- Package-specific compatibility fallbacks stay inside the selected artifact and never override declared dependency edges.
- Standard-import dispatch prioritizes a loaded caller's realm, then context-local scopes, then persistent defaults.
- The opt-in installed-store fallback runs only after ordinary resolution misses a root and preserves exact graph,
  configured-manifest priority, compatibility, native ownership, and no-network boundaries.
- Pure managed versions stay under synthetic identities; the dispatcher delegates unrelated imports unchanged.
- Dynamic `importlib` children may contain non-identifier filename components, while empty components and filesystem path
  syntax remain invalid and lookup stays beneath the selected verified artifact.
- Native request closures use logical process-global identities in `auto` mode, with one compatible public owner per root;
  private helpers follow conventional best-effort import behavior.
- Native isolation and compatibility claims must remain conservative, evidence-based, and explicit about process-global
  state.
- Object-interoperability claims must distinguish conditional boundary likelihood from deliberately selected failure
  examples and direct users toward application-owned primitive contracts.
- Document provenance assertions and decorators as opt-in nominal diagnostics with explicit generated-class,
  object-graph, conversion, and semantic-validation limits.
- Unsafe-loading overrides may relax only known-unsafe classification and strict in-process extension guards; document
  integrity, network, process-backend, and incompatible-owner boundaries as non-overridable.
- Store-only package installation prepares verified targets but does not activate an import realm or mutate an ambient
  environment.

## Work Guidance

- Explain behavior without implying an operating-system sandbox or safe execution of untrusted code.

## Verification

- Use multiversion, identity, namespace, resource, metadata, circular-import, thread, and spawn tests.

## Child DOX Index
