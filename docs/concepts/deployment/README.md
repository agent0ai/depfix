# Deployment modes

## Live

A load call resolves on demand into the platform cache. No repository state is created. Exact request/environment/index
identities have persistent resolution entries; warm processes load them without uv. `refresh=True` replaces a compatible
live resolution atomically.

## Prepared

Run `depfix export`, commit the manifest when reproducibility is desired, and run `depfix install --frozen` before startup.
The ordinary interpreter then discovers the manifest and performs a local graph lookup. Frozen install performs no version
resolution; offline install performs no network access. `--compile-bytecode` prepares bytecode, and `--local` explicitly
copies targets beneath `.depfix/runtime`.

Container layering should copy source and manifest first, install the manifest into a persistent cache layer, then copy
frequently changing application files. Do not copy a developer's entire cache. Serverless builds should install for the
same interpreter/ABI/platform/architecture used at runtime.

## Bundled/air-gapped

Create a `.depfixbundle` on a connected host and transfer it as one file. Offline installation verifies archive safety,
manifest identity, every artifact size/hash, optional runtime-wheel hashes, and target compatibility before atomic cache
promotion. The bundle contains no credential and never invokes a network operation.

## Concurrency

Artifact, target, resolution, and uv-bootstrap mutations use cross-process directory locks and temporary/atomic promotion.
Threaded calls share canonical identities. Temporary `using()` selections are context-local across threads and async tasks.
Spawn workers should use `depfix.multiprocessing_initializer` in their initializer.
