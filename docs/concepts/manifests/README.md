# Resolved import manifests

The default `.depfix/imports.lock` is deterministic TOML format version 1. Its `manifest-id` is the SHA-256 of canonical
environment, resolver, artifact, node, request, policy, and dynamic-diagnostic content.

It records:

- the CPython target, ABI, platform, and architecture;
- Depfix and uv versions;
- normalized requests, APIs, explicit module choice, isolation/index/source policy, source site, and alias;
- exact wheel/module artifacts, sizes, hashes, tags, `Requires-Python`, yanked state, redirect/source/build/Git provenance;
  built artifacts additionally retain the sanitized source URL/final URL, source SHA-256/size, local content identity,
  build backend, and exact resulting wheel identity;
- public/private/all import names, namespace contributions, native classification, extras, and evaluated markers;
- lock-scoped realm nodes and parent-specific dependency edges;
- unresolved dynamic scanner diagnostics.

No credential is permitted. Parsing validates full hashes, graph references, aliases, module names, native policy, target,
and canonical identity before use. A manifest matches a runtime call by normalized request, API, module override, and
isolation—not merely by a version that happens to satisfy the range.

Discovery order is explicit per-call `manifest`, `depfix.configure`, `DEPFIX_MANIFEST`, upward `.depfix/imports.lock`
search within a bounded project/VCS boundary, then live mode. Set `DEPFIX_FROZEN=1` to reject undeclared calls.

The JSON Schema describing TOML's parsed representation is shipped at
`depfix/schemas/depfix-manifest-v1.schema.json` and mirrored in the repository `schemas/` directory.
