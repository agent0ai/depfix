# Basic debugging example

## Purpose

- Provide one executable environment diagnostic for Depfix's public live-loading behavior.

## Ownership

- `application.py` owns the diagnostic checks; `README.md` owns setup and troubleshooting commands.

## Local Contracts

- The default check stays small, pure Python, and based on stable public PyPI releases.
- The extended check covers additional single-module, package, submodule, and resource-access shapes.
- Failures must preserve useful tracebacks or typed Depfix diagnostics.

## Work Guidance

- Keep local debug state under the ignored root `tmp/` when documenting repository-local runs.

## Verification

- Run the default and `--extended` modes in a clean environment with network access.

## Child DOX Index
