# Migration and upgrades

The permanent product surface is `depfix`, `.depfix`, `depfix_imports`, `_depfix`, `DEPFIX_*`, and `.depfixbundle`.
Prototype manifests and source forms are intentionally unsupported because they did not carry the production provenance
and identity fields. Export a new `.depfix/imports.lock` from source.

Use `depfix migrate requirements.txt` or `depfix migrate pyproject.toml` to create reviewable `[[dynamic]]` declarations in
`.depfix/config.toml`. Requirements includes and hashes are understood by `depfix export --requirements`; constraints and
index credentials remain external policy.

Manifest and bundle readers reject unknown format versions. Cache layout is versioned under `v1`, so an incompatible future
layout can coexist rather than mutating immutable state in place.
