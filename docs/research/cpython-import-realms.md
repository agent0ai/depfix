# Draft: CPython import realms and contextual imports

Status: design draft following the validated add-on experiment; not a submitted
PEP and not a CPython patch.

## Problem

The default import cache is keyed by one fully qualified string and nested
imports do not receive first-class referrer identity. The add-on can give
pure-Python code a bound `__import__`, but C extensions commonly call name-only
import APIs, ordinary `importlib` functions lack realm context, and native
library state may be process-global.

New syntax is not required. The semantic primitive is an import realm.

## Python API

```python
realm = importlib.create_realm(resolver=resolver, policy=policy)
module = realm.import_module("requests", identity=artifact_and_node_identity)
```

The default realm preserves today's behavior. A non-default realm owns:

- an immutable `realm_id` and policy;
- `realm.modules`, keyed by logical full name or a first-class `ModuleKey`;
- a resolver that receives exact referrer/module identity;
- standard-library and ambient fallback policy;
- namespace provider sets.

Extend `ModuleSpec` (or attach an immutable `ModuleIdentity`) with
`logical_name`, `canonical_identity`, `distribution_identity`,
`artifact_identity`, `realm` and `referrer`. Logical spelling, cache identity,
origin and distribution metadata remain separate.

## Cache and finder protocol

Internally key non-default caches by `(realm_id, logical_fullname)`. Keep
`sys.modules` as the default realm's string-keyed compatibility view; never put
context-sensitive proxy values under a bare key. Expose non-default caches as
`realm.modules`.

Add a versioned contextual protocol:

```python
find_spec(fullname, path, target=None, *, context: ImportContext)
```

`ImportContext` contains the referrer identity, realm, logical request, relative
level, target identity and policy. Existing finders receive an adapter in the
default realm. `importlib.import_module` and the lower-level bootstrap acquire
optional keyword-only `referrer` and `realm` arguments. Loader execution records
the realm in module identity so imports from that module inherit it directly,
without frame inspection.

The import lock must operate on `ModuleKey`, insert the module in the correct
realm cache before execution, and clean up only the failed key. Namespace
packages are assembled only from providers visible to that realm.

## C API and extension declaration

Add stable APIs conceptually equivalent to:

```c
PyObject *PyImport_ImportModuleFromReferrer(
    PyObject *name,
    PyObject *referrer_module);

PyObject *PyImport_ImportModuleInRealm(
    PyObject *name,
    PyObject *realm);

PyObject *PyModule_GetRealmState(PyObject *module);
```

Existing APIs retain default-realm behavior. Extension authors that need
dependency locality migrate from `PyImport_ImportModule("dependency")` to the
referrer-aware call.

Add a module-definition slot describing independently testable capabilities:
multi-phase initialization, multiple module objects, multiple versions,
subinterpreter support, per-interpreter-GIL/free-threaded support, and contextual
imports. Packaging may later expose an opt-in `Multi-Version-Safe: true` claim,
but runtime certification remains keyed to exact artifact/platform evidence.

PEP 489's multi-phase creation makes independent module objects possible but
does not eliminate process-global library collisions or state:
https://peps.python.org/pep-0489/

## Minimal reference patch sequence

1. Introduce internal `RealmState`, `ImportContext` and `ModuleKey` objects plus
   a default-realm adapter around `sys.modules`.
2. Thread `ImportContext` through `importlib._bootstrap` find/load functions and
   add the backward-compatible finder adapter.
3. Store realm/identity on `ModuleSpec`; make Python nested imports inherit it.
4. Add public `importlib.create_realm`, contextual `import_module`, and
   `realm.modules`.
5. Add the three C APIs and extension capability slot, with default-realm
   wrappers for all existing calls.
6. Add tests for circular imports, reload/failure cleanup, namespaces,
   subinterpreters, C referrer imports and unchanged default-realm performance.

## Compatibility and evaluation

Ordinary imports, `sys.modules`, legacy finders/loaders and existing C calls must
remain unchanged in the default realm. The patch needs import microbenchmarks,
startup measurements, memory accounting per realm, audit-event definitions,
pickle/multiprocessing bootstrapping, debugger/introspection behavior and a
deprecation/migration story for realm-sensitive name-only C imports.

Syntax such as `import package@1.2` remains out of scope until identity, cache
and referrer semantics have ecosystem experience.
