# Benchmarks

## Purpose

- Hold small, reproducible probes for Depfix runtime behavior and performance.

## Ownership

- `import_identity.py` measures repeated public `import_module()` identity and prepared warm-path timing.
- `native_compatibility.py` runs representative native-package operations in fresh processes; Torch is opt-in because its
  locked dependency graph requires multiple gigabytes.

## Local Contracts

- Benchmarks must assert correctness before reporting timing.
- Results are diagnostic and must not be presented as universal performance guarantees.
- Native compatibility cases use fresh processes so process-shared modules cannot contaminate later cases.

## Work Guidance

- Keep probes standalone and runnable against an installed or editable Depfix package.

## Verification

- Run `python benchmarks/import_identity.py MANIFEST SPECIFIER` when runtime caching or module identity changes.
- Run `python benchmarks/native_compatibility.py --cache-dir CACHE` when native/shared compatibility changes; add
  `--include-torch` only on a host with sufficient disk and network capacity.

## Child DOX Index
