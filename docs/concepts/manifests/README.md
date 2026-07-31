# Resolved import manifests

The default `.depfix/imports.lock` is deterministic TOML format version 1. Its `manifest-id` is the SHA-256 of canonical
environment, resolver, artifact, node, request group, request, policy, and dynamic-diagnostic content.

It records:

- the CPython target, ABI, platform, and architecture;
- Depfix and uv versions;
- normalized requests, APIs, explicit module choice, isolation/index/source policy, source site, and alias;
- grouped `default()`, `using()` context, and `using()` decorator declarations, including their complete specifier set,
  source/enclosing function, directly associated imports, generated aliases, provided roots, source base directory,
  isolation/options, and resolved realm identity;
- exact wheel/module artifacts, sizes, hashes, tags, `Requires-Python`, yanked state, redirect/source/build/Git provenance;
  built artifacts additionally retain the sanitized source URL/final URL, source SHA-256/size, local content identity,
  build backend, and exact resulting wheel identity;
- public/private/all import names, namespace contributions, native classification, extras, and evaluated markers;
- lock-scoped realm nodes and parent-specific dependency edges;
- unresolved dynamic scanner diagnostics.

No credential is permitted. Parsing validates full hashes, graph references, aliases, module names, native policy, target,
and canonical identity before use. A manifest matches explicit runtime calls by normalized request, API, module override,
and isolation. Standard imports match the exact normalized group, declaration mode, and isolation. Frozen execution rejects
unlisted defaults and scopes and never resolves a new version.

Discovery order is explicit per-call `manifest`, `depfix.configure`, `DEPFIX_MANIFEST`, upward `.depfix/imports.lock`
search within a bounded project/VCS boundary, then live mode. Set `DEPFIX_FROZEN=1` to reject undeclared calls.

The JSON Schema describing TOML's parsed representation is shipped at
`depfix/schemas/depfix-manifest-v1.schema.json` and mirrored in the repository `schemas/` directory.
