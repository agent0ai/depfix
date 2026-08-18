# Threat model

Depfix treats package indices, remote sources, archives, manifests, bundles, cache contents, and project source declarations
as potentially malformed. It does not claim that executing a validated third-party package is safe; package code receives
the normal Python process authority.

Controls include:

- SHA-256 content addressing, size checks, wheel identity/Core Metadata checks, complete archive-derived payload
  manifests, and verification of every secure hash or size assertion supplied by wheel `RECORD`;
- HTTPS-only remote artifact downloads, explicit frozen hashes, redirect provenance, bounded resumable retries, and secret
  redaction; exact size and SHA-256 checks still gate promotion after every retry;
- traversal, absolute/drive path, backslash, special-file, duplicate/case/namespace-collision, file-count, and expanded-size
  rejection; contained source-archive links resolve only to archive regular files and are materialized without filesystem
  links, with copied contents included in the expanded-size bound;
- canonical manifest/bundle identities and complete graph-reference validation;
- no credential serialization, optional index/host allowlists, redirect validation, and first-index uv policy to reduce
  dependency-confusion exposure;
- temporary construction, cross-process locks, read-only immutable targets, and atomic promotion;
- exact warm-reuse comparison against the complete payload file and directory namespace, rejecting mutation, omission,
  or unmanifested paths, links, and special entries; unmanifested interpreter bytecode is excluded only when its header
  and complete executable body exactly match deterministic recompilation of manifested source;
- no `sys.path` changes or ambient third-party fallback for in-process realms;
- guarded shared mode prepends only verified cache targets, owns public/requested roots exactly, rejects incompatible
  replacement, and treats private top-level helpers as conventional process-global best effort;
- denial by default of native extension loading in strict in-process realms and packages classified as known unsafe;
  explicit `allow_unsafe` requests record and accept that reduced isolation without weakening artifact or network checks;
- shared `using()` scopes limit lookup visibility but retain native process ownership after exit;
- offline/frozen enforcement before resolution or network work.

Residual risks include malicious Python code, compromised build backends during permitted source builds, compromised
credentials supplied by external tools, denial of service below configured limits, local users able to mutate the same
cache account, native ABI/system-library incompatibility, process-global private-helper collisions in shared mode, and
package behavior that depends on unsupported discovery. Unsafe loading is an execution-policy opt-in, not a sandbox escape
control: enabled package code has normal process authority. Prefer exact wheels, trusted indices, frozen manifests, offline
bundles, fresh workers for native version changes, least-privilege build workers, and isolated operating-system identities
for untrusted packages.

Report vulnerabilities according to [SECURITY.md](../../SECURITY.md), without including live credentials or private package
contents.
