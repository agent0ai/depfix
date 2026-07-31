# Import-module discovery

Depfix reads Core Metadata 2.5 `Import-Name` and `Import-Namespace` first, including dotted names, `private`, empty
`Import-Name`, and exclusive/shared consistency. `Import-Namespace` alone is never treated as the primary public API.

When exclusive metadata is absent, the exact wheel is inspected. Depfix considers purelib/platlib paths, packages, root
modules, namespace leaves, stub-only modules, and native extension suffixes while ignoring metadata, scripts, data,
invalid identifiers, caches, and obvious private roots. Thus project and import names need not match (`PyYAML`/`yaml`,
`Pillow`/`PIL`).

The deterministic result keeps separate public candidates, private names, all importable names, filesystem provider roots,
and namespace contributions. Exact-hash results are cached under `v1/metadata/imports`. User `module=` remains authoritative
after validation. Ambiguity is an error; Depfix never chooses alphabetically or changes `import_module`'s return type.
