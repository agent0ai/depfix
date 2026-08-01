# Benchmarks

## Purpose

- Hold small, reproducible probes for Depfix runtime behavior and performance.

## Ownership

- `import_identity.py` measures repeated public `import_module()` identity and prepared warm-path timing.

## Local Contracts

- Benchmarks must assert correctness before reporting timing.
- Results are diagnostic and must not be presented as universal performance guarantees.

## Work Guidance

- Keep probes standalone and runnable against an installed or editable Depfix package.

## Verification

- Run `python benchmarks/import_identity.py MANIFEST SPECIFIER` when runtime caching or module identity changes.

## Child DOX Index
