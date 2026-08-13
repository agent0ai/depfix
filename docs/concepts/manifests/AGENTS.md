# Resolved manifests

## Purpose

- Document deterministic, target-specific records of a resolved Depfix graph.

## Ownership

- `README.md` owns manifest identity, content, discovery, validation, and compatibility semantics.

## Local Contracts

- Manifests contain no credentials and bind requests and grouped standard-import declarations to exact target, resolver,
  artifact, node, module, source, isolation, and policy state.
- Store-only grouped installations use the additive `package-install` request mode and remain reloadable exact graphs.
- Unknown format versions and non-canonical graph identities are rejected.
- Prepared manifests remain exact and reject live index arguments rather than implying that already-locked artifacts will
  be re-resolved.

## Work Guidance

- Update schema copies and migration guidance with manifest-contract changes.

## Verification

- Use deterministic round-trip, corruption, mismatch, frozen, and schema validation tests.

## Child DOX Index
