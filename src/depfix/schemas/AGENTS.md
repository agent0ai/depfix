# Packaged schemas

## Purpose

- Ship manifest validation contracts with the installed Depfix package.

## Ownership

- JSON schemas loaded through package resources belong here.

## Local Contracts

- Files must match their public counterparts under root `schemas/` byte for byte.
- Package data declarations in `pyproject.toml` must continue to include this folder.

## Work Guidance

- Update the public and packaged copies in the same change.

## Verification

- Compare both schema copies and inspect wheel contents through `python scripts/release_check.py`.

## Child DOX Index
