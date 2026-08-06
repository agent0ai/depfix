# Artifacts and cache

## Purpose

- Document immutable package content, integrity checks, and safe local materialization.

## Ownership

- This folder owns the content-addressed cache model and artifact lifecycle.

## Local Contracts

- Blob identity is SHA-256 content identity; completed extracted targets are environment-specific and read-only.
- Cache mutation uses bounded input, locks, temporary construction, verification, and atomic promotion.
- Staging roots remain owner-writable only through promotion for Darwin compatibility; completed roots are hardened.
- Lifecycle metadata is mutable and separate from package content: preserve the first installation time, mark successful
  imports, include retained artifact/build/target size, and keep cleanup from removing reserved or live-leased packages.
- Installation provenance is secret-redacted and graph-aware: deduplicate equivalent origins, retain command or source
  path/line reasons, distinguish content identity from same-version artifact variants, and support flat, duplicate, and
  top-down dependency-tree inspection without the originating project files.
- Automatic retention defaults to 30 unused days, runs at most daily outside the foreground import path, and shares its
  selection/removal rules with the Python and CLI cache APIs.

## Work Guidance

- Distinguish immutable artifacts from realm nodes and request-resolution entries.

## Verification

- Use cache, wheel-safety, bundle, and concurrency tests for changed claims.

## Child DOX Index
