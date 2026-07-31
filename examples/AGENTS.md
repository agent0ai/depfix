# Examples

## Purpose

- Provide minimal, copyable examples of public Depfix workflows.

## Ownership

- Each child folder owns one independently runnable scenario.

## Local Contracts

- Examples use public APIs and supported CLI commands only.
- Version pins should demonstrate behavior without silently becoming package policy.

## Work Guidance

- Prefer small applications with an obvious expected result.

## Verification

- Run the example in an isolated temporary environment when its API or packaging inputs change.

## Child DOX Index

- [`conflicting_botocore_versions/AGENTS.md`](conflicting_botocore_versions/AGENTS.md) — incompatible AWS CLI and Boto3 Botocore realms.
- [`container/AGENTS.md`](container/AGENTS.md) — prepared container image layering.
- [`debug_basic/AGENTS.md`](debug_basic/AGENTS.md) — environment diagnostics and basic live realm checks.
- [`two_idna_versions/AGENTS.md`](two_idna_versions/AGENTS.md) — two versions of one distribution in one process.
