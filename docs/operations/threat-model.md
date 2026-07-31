# Threat model

Depfix treats package indices, remote sources, archives, manifests, bundles, cache contents, and project source declarations
as potentially malformed. It does not claim that executing a validated third-party package is safe; package code receives
the normal Python process authority.

Controls include:

- SHA-256 content addressing, size checks, wheel identity/Core Metadata checks, and full wheel `RECORD` validation;
- HTTPS-only remote artifact downloads, explicit frozen hashes, redirect provenance, bounded reads, and secret redaction;
- traversal, absolute/drive path, backslash, link/device, duplicate/case-collision, file-count, and expanded-size rejection;
- canonical manifest/bundle identities and complete graph-reference validation;
- no credential serialization, optional index/host allowlists, redirect validation, and first-index uv policy to reduce
  dependency-confusion exposure;
- temporary construction, cross-process locks, read-only immutable targets, and atomic promotion;
- no changes to `sys.path` or active `site-packages` and no ambient third-party import fallback;
- rejection of unknown native module loading in an in-process realm;
- offline/frozen enforcement before resolution or network work.

Residual risks include malicious Python code, compromised build backends during permitted source builds, compromised
credentials supplied by external tools, denial of service below configured limits, local users able to mutate the same
cache account, and package behavior that depends on unsupported process-global discovery. Prefer exact wheels, trusted
indices, frozen manifests, offline bundles, least-privilege build workers, and isolated operating-system identities for
untrusted packages.

Report vulnerabilities according to [SECURITY.md](../../SECURITY.md), without including live credentials or private package
contents.
