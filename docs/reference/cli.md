# CLI reference

Global options (`--json`, `--quiet`, `--verbose`, `--cache-dir`, `--prefer-newest`) may be placed before or directly after
a primary command. `--prefer-newest` ignores compatible cached versions when ranking candidates and selects the newest
compatible versions available from the configured indexes. `depfix --help` and `depfix --version` are stable.

Primary workflow:

- `depfix export [PROJECT] [-o MANIFEST]`: scan, resolve, build as permitted, cache, generate IDE metadata, and write a
  deterministic manifest. Static `default()` and `using()` declarations remain grouped. Add dynamic requests with
  `--include`, exclude paths with `--exclude`, or incorporate roots with `--requirements`. Without `--prefer-newest`,
  compatible artifacts already in the shared cache are preferred.
- `depfix install MANIFEST_OR_BUNDLE [--frozen] [--offline] [--no-build]`: validate exact state and materialize packages.
  `--cached-only` forbids downloads. `--local` copies prepared targets beneath `.depfix/runtime`; combine it with
  `--target PATH` to choose another destination. `--compile-bytecode` prepares bytecode. `--python`, `--platform`, and
  `--architecture` reject mismatched targets; cross-target installation is not implemented.
- `depfix bundle MANIFEST -o FILE.depfixbundle`: create a deterministic air-gap archive. Add
  `--include-depfix-runtime` for disconnected bootstrapping wheels.
- `depfix prepare [PROJECT]`: export, install, verify, and generate IDE data in one development command.

Inspection and operation:

- `scan`, `check`, `verify`, `tree`, `show`, `why`, `list`, `doctor`.
- `fetch SPECIFIER` prepares a live request.
- `run SCRIPT [ARGS...]` or `run -m MODULE [ARGS...]` activates optional prepared state before execution.
- `migrate requirements.txt` or `migrate pyproject.toml` creates reviewable dynamic declarations.
- `requirements export MANIFEST --realm NODE --output FILE` emits one realm with exact hashes.
- `pip install PACKAGE...` and `pip install -r FILE` resolve roots as one Depfix group and populate the shared store. They
  never mutate the active environment or add store paths to `sys.path`. Incompatible transitive requirements get separate
  dependency nodes instead of forcing one environment-wide version. `-c/--constraint`, nested requirement/constraint
  files, `--index-url`, repeated `--extra-index-url`, hashes, local `-e` paths, `--offline`, and `--refresh` are supported.
  `-U/--upgrade` is equivalent to `--prefer-newest`. Unsupported pip environment options fail explicitly rather than
  being forwarded. `depfix pip --version` reports the uv backend version.
- `cache dir` prints the shared cache path. `cache list` reports each installed distribution/version with its artifact
  hash, UTC installation and last-use timestamps, and total bytes.
- `cache cleanup [--days N] [--dry-run]` removes inactive artifacts older than the configured 30-day default.
- `cache remove PACKAGE [--version VERSION] [--artifact SHA256] [--dry-run]` removes an exact package selection while
  preserving artifacts being prepared or leased by active runtimes.
- `cache verify|prune|clean` retains the low-level integrity, manifest-reference, and complete-root maintenance commands.

IDE commands are `ide sync`, `path`, `configure`, `attach`, `detach`, `status`, and `clean`. Attaching is allowed only in an
active virtual environment and writes a graph-specific `.pth`; `detach` removes it. Generated configuration puts the
optional ordinary-default overlay before the graph-specific `depfix_imports` aliases when that overlay exists.

Expected errors have no traceback unless `--verbose` is requested. JSON failures contain `ok`, exception type, and a
secret-redacted message. Argument-parser errors use exit code 2.
Interactive resolution and installation progress is written to stderr. `--quiet` and `--json` suppress progress;
`--verbose` retains it and enables tracebacks for failures.
