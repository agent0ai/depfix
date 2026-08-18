# uv backend

The official `uv` distribution is a mandatory runtime dependency (`uv>=0.11.0`). Depfix invokes documented executable
commands only; it imports no uv internals and vendors no uv binary.

Lookup order is explicit configuration/`DEPFIX_UV`, the console script beside the current interpreter, the environment's
scripts directory, `PATH`, then the private Depfix tool cache. Every candidate must return a compatible `uv --version`.

Broken `--no-deps` installations may bootstrap exact uv 0.11.0 into `v1/tools/uv/<version>/<platform-key>`. Bootstrap uses
the current interpreter and pip in a temporary virtual environment, preserving the interpreter layout required by
dynamically linked uv-managed Python builds. It uses a cross-process lock, validation, and atomic activation. Bootstrap
never writes to application `site-packages`, never runs a downloaded shell script, and is disabled by frozen/offline policy.

Commands use isolated targets/build directories, the current Python, no Python downloads, no config discovery, first-index
behavior, and configured indexes. Depfix disables uv's animated progress, captures its output for reliable errors, and
forwards successful package summaries through its secret-redacted stderr progress channel. The manifest records the
executing uv version. Each prepare, resolve, or build command overrides ambient uv cache configuration with a unique
process-owned directory beneath Depfix's temporary cache root and removes it when the subprocess returns. If Depfix is
killed, a later install or cache-cleanup pass reclaims that directory only after its owner process is gone and the age
grace has elapsed. Depfix never cleans or writes the global cache used by direct user invocations of uv. CI tests the
minimum and a current compatible release.

Multi-root registry operations use `uv pip compile` as an exact-version planning boundary. Compilation does not install
the resolved closure. Depfix downloads, verifies, inspects, and materializes selected artifacts once through its own store,
and preserves isolated fallback for roots that cannot share a conventional single-version plan. In ordinary cache-first
mode, verified installed inventory is supplied as exact constraints and short-lived metadata-only wheel overrides, so a
custom-index or VCS local version can participate without being rediscovered on the active index. These stubs contain only
the stored Core Metadata needed by uv, are removed with the planning directory, and are never materialization inputs. A
failure caused by the optional installed preference set retries all roots without those preferences. If the root group
still conflicts, Depfix recursively splits it in stable order, keeps each successful half as its own exact plan, and sends
only failed singleton roots to isolated resolution. The split tree needs at most linear planning calls and preserves
different dependency versions across successful cohorts without a global maximum-subset search.
