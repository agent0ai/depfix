# Security policy

## Supported versions

Until the first public release, only the current `0.1.x` source line receives security fixes. This document will be updated
when another maintained line exists.

## Reporting

There is no owner-approved private security contact in the repository. Before publication, the owner must add the intended
private reporting channel. Until then, do not post live credentials, private index URLs, proprietary packages, or exploit
details in a public issue; contact the repository owner through an already established private channel.

Include the Depfix version, Python/uv versions, platform, source kind, frozen/offline state, and a minimal redacted
reproduction. `depfix doctor --json` is useful after review.

## Scope

Relevant issues include artifact/manifest/bundle verification bypasses, archive traversal or links, credential disclosure,
unexpected network access in offline mode, resolution in frozen mode, cache race/collision attacks, ambient dependency
leaks, and unsafe native-module loading. Malicious behavior inherent to intentionally executed third-party Python code is
outside the isolation claim, though validation escapes remain in scope.

See [the threat model](docs/operations/threat-model.md) for controls and residual risks.
