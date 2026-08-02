# Import realms

## Purpose

- Document Depfix's isolated pure-Python realms and guarded process-shared native import model.

## Ownership

- `README.md` owns runtime architecture; `compatibility.md` owns supported and unsupported import behavior.

## Local Contracts

- Logical module spelling, canonical module identity, artifact identity, node identity, and realm identity stay distinct.
- Realm code resolves third-party imports only through declared parent-specific edges.
- Package-specific compatibility fallbacks stay inside the selected artifact and never override declared dependency edges.
- Standard-import dispatch prioritizes a loaded caller's realm, then context-local scopes, then persistent defaults.
- Pure managed versions stay under synthetic identities; the dispatcher delegates unrelated imports unchanged.
- Native request closures use logical process-global identities in `auto` mode, with one compatible public owner per root;
  private helpers follow conventional best-effort import behavior.
- Native isolation and compatibility claims must remain conservative, evidence-based, and explicit about process-global
  state.
- Unsafe-loading overrides may relax only known-unsafe classification and strict in-process extension guards; document
  integrity, network, process-backend, and incompatible-owner boundaries as non-overridable.

## Work Guidance

- Explain behavior without implying an operating-system sandbox or safe execution of untrusted code.

## Verification

- Use multiversion, identity, namespace, resource, metadata, circular-import, thread, and spawn tests.

## Child DOX Index
