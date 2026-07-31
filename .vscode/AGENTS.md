# VS Code configuration

## Purpose

- Provide a small shared debugger setup for repository Python files.

## Ownership

- `launch.json` owns the checked-in debug configurations.

## Local Contracts

- `Debug current file` launches the active file with VS Code's selected Python interpreter and the repository as `cwd`.
- Debug sessions use the integrated terminal and expose dependency internals with `justMyCode: false`.
- Do not load `.env` automatically; publication credentials must not be inherited by arbitrary debug targets.

## Work Guidance

- Keep launch entries portable across operating systems and avoid application-specific arguments on the current-file entry.

## Verification

- Parse `launch.json` as JSON and launch `examples/debug_basic/application.py` with the Python debugger.

## Child DOX Index
