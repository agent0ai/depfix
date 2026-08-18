# pip and uv compatibility-gate audit

This bounded audit was performed on 2026-08-18 against pip 26.2, uv 0.12.5, the current PyPA specifications, and the
Depfix 0.11.1 acquisition, source-build, wheel-inspection, requirement-normalization, and materialization paths. It does
not extend Depfix to new VCS backends or distribution formats.

The triggering `tinysegmenter==0.3` source archive contains `tinysegmenter-0.3/README` as a tar symlink to the internal
regular file `README.rst`. Clean pip and uv wheel builds both succeed. Depfix 0.11.1 rejected the archive before extraction.

## Dispositions

| Gate | pip/uv and specification evidence | Classification | Disposition |
| --- | --- | --- | --- |
| All source-archive links rejected | pip 26.2 and uv 0.12.5 build `tinysegmenter==0.3`; pip's tar extraction uses Python's containment-aware data filter. | Unnecessary overrestriction | Validate the complete archive namespace, resolve contained tar symlinks/hard links and ZIP symlink metadata, and copy final regular-file contents. Absolute, escaping, dangling, cyclic, ambiguous, directory, and special-file targets still fail before extraction. |
| Source executable bits discarded | pip preserves executability for regular archive members; build inputs can contain executable helpers. | Unnecessary overrestriction | Preserve owner execution on regular files and materialized links whose final target is executable. Other permission bits remain staging defaults. |
| Index size required through a separate HEAD response | Size is optional in the Simple API; pip and uv can stream a bounded download without it. | Unnecessary overrestriction | Treat missing size as unknown, enforce the existing streaming limit and SHA-256, then record the observed size. Advertised sizes remain exact checks. |
| Yanked artifact rejected after uv selected an exact version | The yanking specification recommends permitting exact `==`/`===` pins while excluding yanked files from ordinary range selection. | Unnecessary overrestriction | Permit a yanked artifact for an exact pin and retain its yanked state/reason; unpinned selection still excludes it. |
| Wheel link mode bits rejected | Wheels are ZIP archives installed by unpacking; pip and uv materialize the entry payload as a regular file rather than creating a filesystem link. | Unnecessary overrestriction | Ignore ZIP link mode bits, verify the exact payload through RECORD, and write an ordinary file. No wheel link is created or followed. |
| Wheel RECORD limited to SHA-256 | The wheel specification permits SHA-256 or stronger hashes; pip and uv accept SHA-512 RECORD rows. | Unnecessary overrestriction | Verify Python-guaranteed SHA-2, SHA-3, and BLAKE2 algorithms of at least 256-bit strength. MD5, SHA-1, SHA-224, unknown, missing, and mismatched hashes remain rejected. |
| Archive absolute/traversal/drive/backslash paths, duplicate or case-folding collisions, and file/directory namespace collisions rejected | pip and uv enforce containment but can differ by platform on separator and case behavior. | Required Depfix invariant | Retain and strengthen pre-write namespace validation because one immutable target must be safe and deterministic on Linux, macOS, and Windows. |
| Device, FIFO, socket, unknown special members, and directory link targets rejected | pip's data filter rejects dangerous special entries; no supported package build needs them. | Equivalent security expressed differently | Retain. Depfix source staging accepts only directories, regular files, and links provably resolving to regular files. |
| File-count and expanded-size limits | pip/uv extraction is bounded by host resources rather than Depfix's fixed limits. | Required Depfix invariant | Retain for the shared service/store threat model. Materialized link copies now count toward the expanded-size limit. |
| Complete wheel RECORD coverage, size/hash matching, artifact SHA-256, filename/metadata identity, and compatible tags required | pip and uv accept some malformed RECORD details in practice, while the wheel specification requires hash verification. | Required Depfix invariant | Retain. Depfix promotes immutable, content-addressed targets and later trusts their verified payload inventory. |
| HTTPS defaults, redirect checks, allowlists, exact frozen hashes, and provenance | pip/uv allow broader operator configuration. | Required Depfix invariant | Retain as explicit network and prepared/offline guarantees; controlled HTTP remains an opt-in development policy. |
| Strict wheel/archive path portability and native-package loading policy | pip/uv install into one environment and do not provide Depfix's cross-platform store or multiversion runtime guarantees. | Required Depfix invariant | Retain cross-platform namespace restrictions and one compatible process owner for native imports. |
| PEP 508/440 parsing plus narrow legacy `Requires-Python` repair | Current pip/uv reject unrelated malformed requirements; Depfix already limits repair to unambiguous historical numeric ordering wildcards. | Equivalent security expressed differently | Retain. No additional requirement-normalization incompatibility was reproduced. |

## Evidence boundary

- Real artifact: PyPI `tinysegmenter-0.3.tar.gz`, including member type, target, pip build, uv build, Depfix failure, and
  corrected Depfix build/materialization.
- Synthetic tar and ZIP fixtures: forward and nested relative links, hard links, link chains, case and type collisions,
  absolute/escaping/dangling/cyclic/directory targets, Windows separators/drives, devices, FIFO/socket types, and link
  expansion limits.
- Synthetic wheels: SHA-512 RECORD rows and link mode bits were installed with pip 26.2 and uv 0.12.5, then exercised
  through Depfix's stricter RECORD and immutable-target path.
- Authoritative references: [pip archive extraction](https://github.com/pypa/pip/blob/main/src/pip/_internal/utils/unpacking.py),
  [Simple repository API](https://packaging.python.org/en/latest/specifications/simple-repository-api/),
  [file yanking](https://packaging.python.org/en/latest/specifications/file-yanking/), and
  [wheel format](https://packaging.python.org/en/latest/specifications/binary-distribution-format/).

The audit did not find another unnecessary compatibility rejection within the supported acquisition/build/materialization
boundary. It intentionally did not treat unsupported package formats, additional VCS backends, resolver semantics,
execution sandboxing, or native multiversion isolation as pip-compatibility work.
