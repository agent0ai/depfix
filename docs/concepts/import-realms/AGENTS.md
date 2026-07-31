# Import realms

## Purpose

- Document Depfix's in-process pure-Python module identity and dependency isolation model.

## Ownership

- `README.md` owns runtime architecture; `compatibility.md` owns supported and unsupported import behavior.

## Local Contracts

- Logical module spelling, canonical module identity, artifact identity, node identity, and realm identity stay distinct.
- Realm code resolves third-party imports only through declared parent-specific edges.
- Standard-import dispatch prioritizes a loaded caller's realm, then context-local scopes, then persistent defaults.
- Managed versions stay under synthetic identities; the dispatcher delegates unrelated imports unchanged.
- Native isolation claims must remain conservative and evidence-based.

## Work Guidance

- Explain behavior without implying an operating-system sandbox or safe execution of untrusted code.

## Verification

- Use multiversion, identity, namespace, resource, metadata, circular-import, thread, and spawn tests.

## Child DOX Index
