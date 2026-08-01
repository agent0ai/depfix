# Security policy

## Supported versions

Only the newest minor release line receives security fixes.

| Version | Supported |
| --- | --- |
| `0.2.x` | Yes |
| `0.1.x` | No |

## Reporting

Report vulnerabilities privately to [pr@agent-zero.ai](mailto:pr@agent-zero.ai). Do not post live credentials, private
index URLs, proprietary packages, or exploit details in a public issue.

Include the Depfix version, Python/uv versions, platform, source kind, frozen/offline state, and a minimal redacted
reproduction. `depfix doctor --json` is useful after review.

## Scope

Relevant issues include artifact/manifest/bundle verification bypasses, archive traversal or links, credential disclosure,
unexpected network access in offline mode, resolution in frozen mode, cache race/collision attacks, ambient dependency
leaks, and unsafe native-module loading. Malicious behavior inherent to intentionally executed third-party Python code is
outside the isolation claim, though validation escapes remain in scope.

See [the threat model](docs/operations/threat-model.md) for controls and residual risks.
