# Deployment

## Purpose

- Document when resolution occurs and how exact state moves into connected, prepared, and disconnected environments.

## Ownership

- `README.md` owns live, prepared, bundle, air-gap, container, serverless, and concurrency deployment semantics.

## Local Contracts

- Frozen mode performs no version resolution; offline mode performs no network access.
- Bundles contain exact verified content and no credentials.

## Work Guidance

- State build-host/runtime target requirements and cache side effects explicitly.

## Verification

- Use prepared-startup, offline-install, deterministic-bundle, and air-gap release checks.

## Child DOX Index
