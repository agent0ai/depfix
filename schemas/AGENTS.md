# Public schemas

## Purpose

- Publish machine-readable contracts for Depfix files consumed outside the Python package.

## Ownership

- The root manifest schema is the source copy for external tooling and documentation.

## Local Contracts

- Schema format versions are compatibility boundaries.
- Keep the packaged schema in `src/depfix/schemas/` byte-for-byte synchronized.
- Record unsafe-loading decisions additively on requests and request groups so prepared execution preserves policy intent.
- Accept additive store-only `package-install` requests without weakening existing format-v1 import request validation.

## Work Guidance

- Prefer additive changes within a format version; use migration guidance for breaking changes.

## Verification

- Run manifest tests and compare the public and packaged schema files.

## Child DOX Index
