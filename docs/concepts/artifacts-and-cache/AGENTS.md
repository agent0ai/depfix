# Artifacts and cache

## Purpose

- Document immutable package content, integrity checks, and safe local materialization.

## Ownership

- This folder owns the content-addressed cache model and artifact lifecycle.

## Local Contracts

- Blob identity is SHA-256 content identity; completed extracted targets are environment-specific and read-only.
- Cache mutation uses bounded input, locks, temporary construction, verification, and atomic promotion.
- Staging roots remain owner-writable only through promotion for Darwin compatibility; completed roots are hardened.

## Work Guidance

- Distinguish immutable artifacts from realm nodes and request-resolution entries.

## Verification

- Use cache, wheel-safety, bundle, and concurrency tests for changed claims.

## Child DOX Index
