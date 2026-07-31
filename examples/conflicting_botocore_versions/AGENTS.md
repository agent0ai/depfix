# Conflicting transitive dependencies example

## Purpose

- Prove that two packages with mutually exclusive Botocore requirements can run in one Python process.

## Ownership

- `application.py` is the executable proof; `README.md` explains the A-to-C and B-to-C conflict.

## Local Contracts

- Use published, pinned AWS CLI and Boto3 releases with non-overlapping Botocore constraints.
- Resolve and import both realms without credentials, AWS metadata lookup, or service calls.
- Assert the two Botocore versions and synthetic module identities are distinct.

## Work Guidance

- Keep the output explicit about packages A, B, and shared dependency C.

## Verification

- Run `python examples/conflicting_botocore_versions/application.py` with network access or a warm cache.

## Child DOX Index
