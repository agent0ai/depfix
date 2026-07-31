# CLI reference

Global output options (`--json`, `--quiet`, `--verbose`, `--cache-dir`) may be placed before or directly after a primary
command. `depfix --help` and `depfix --version` are stable.

Primary workflow:

- `depfix export [PROJECT] [-o MANIFEST]`: scan, resolve, build as permitted, cache, generate IDE metadata, and write a
  deterministic manifest. Add requests with `--include`, exclude paths with `--exclude`, or incorporate roots with
  `--requirements`.
- `depfix install MANIFEST_OR_BUNDLE [--frozen] [--offline]`: validate exact state and materialize realms. Deployment
  options include `--cached-only`, `--local`, `--target`, and `--compile-bytecode`.
- `depfix bundle MANIFEST -o FILE.depfixbundle`: create a deterministic air-gap archive. Add
  `--include-depfix-runtime` for disconnected bootstrapping wheels.
- `depfix prepare [PROJECT]`: export, install, verify, and generate IDE data in one development command.

Inspection and operation:

- `scan`, `check`, `verify`, `tree`, `show`, `why`, `list`, `doctor`.
- `fetch SPECIFIER` prepares a live request.
- `run SCRIPT [ARGS...]` or `run -m MODULE [ARGS...]` activates optional prepared state before execution.
- `migrate requirements.txt` or `migrate pyproject.toml` creates reviewable dynamic declarations.
- `requirements export MANIFEST --realm NODE --output FILE` emits one realm with exact hashes.
- `pip ...` delegates conventional environment work to `uv pip`; it does not create a Depfix realm.
- `cache dir|list|verify|prune|clean` inspects or explicitly maintains the global cache.

IDE commands are `ide sync`, `path`, `configure`, `attach`, `detach`, `status`, and `clean`. Attaching is allowed only in an
active virtual environment and writes a graph-specific `.pth`; `detach` removes it.

Expected errors have no traceback unless `--verbose` is requested. JSON failures contain `ok`, exception type, and a
secret-redacted message. Argument-parser errors use exit code 2; delegated `depfix pip` returns uv's exit code.
Interactive resolution and installation progress is written to stderr. `--quiet` and `--json` suppress progress;
`--verbose` retains it and enables tracebacks for failures.
