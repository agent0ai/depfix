# Artifacts and cache

## Purpose

- Document immutable package content, integrity checks, and safe local materialization.

## Ownership

- This folder owns the content-addressed cache model and artifact lifecycle.

## Local Contracts

- Artifact identity is SHA-256 content identity; downloaded blobs are ephemeral, while completed extracted targets are
  environment-specific, read-only, and reusable across projects.
- Cache mutation uses bounded input, locks, temporary construction, verification, and atomic promotion.
- Staging roots remain owner-writable only through promotion for Darwin compatibility; completed roots are hardened.
- Lifecycle metadata is mutable and separate from package content: preserve the first installation time, mark successful
  imports, include retained operational target size, and transactionally renew active dependency closures by exact
  artifact hash without process/runtime identity in the usage store. Explicit uninstall additionally uses removable
  process leases whose establishment and installed-target validation serialize with deletion, so uninstall cannot delete
  an artifact held by a live runtime or activate a runtime over a concurrently removed target.
- Installation provenance is secret-redacted and graph-aware: deduplicate equivalent origins, retain command or source
  path/line reasons, distinguish content identity from same-version artifact variants, and support flat, duplicate, and
  top-down dependency-tree inspection without the originating project files.
- Present materialized package targets as installed packages through top-level list/tree commands; reserve cache-oriented
  inspection terminology for reusable live-resolution records and maintenance operations.
- Automatic retention defaults to 30 unused days, runs at most daily outside the foreground import path, and uses a
  durable candidate plus configurable grace before deletion. Renewal avoids per-artifact filesystem mutation, and final
  deletion revalidates against the transactional usage store under artifact/target locks; explicit cleanup remains
  immediate.
- Explicit uninstall matches only named distributions and PEP 440 constraints, never cascades into dependencies, honors
  preparation/runtime protection, and leaves immutable exact manifests available for online reacquisition or clear
  offline failure.

## Work Guidance

- Distinguish immutable artifacts from realm nodes and request-resolution entries.

## Verification

- Use cache, wheel-safety, bundle, and concurrency tests for changed claims.

## Child DOX Index
