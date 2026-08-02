# Reference

## Purpose

- Define exact public Python and command-line interfaces.

## Ownership

- `api.md` owns Python signatures, return contracts, configuration, and exceptions.
- `cli.md` owns commands, options, and output behavior.

## Local Contracts

- Reference names and defaults must match implementation help and signatures.
- Document `default()` and `using()` as the standard-import APIs; deprecated prototype activation is not a preferred API.
- Keep `depfix.configure()` documented as the canonical process-wide Python configuration entry point and distinguish
  effective defaults from inheriting per-call `None` values.

## Work Guidance

- Prefer precise declarative wording and link to guides for longer workflows.

## Verification

- Compare Python reference with exported call signatures and CLI reference with `depfix --help` plus subcommand help.

## Child DOX Index
