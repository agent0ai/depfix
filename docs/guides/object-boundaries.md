# Guard objects passed between package versions

Two versions can run safely without sharing their library-owned objects. Guard the places where callbacks, plugins,
middleware, caches, or registries could pass those objects from one version to another.

## Detect the producer

`realm_of()` reports immutable provenance when an object's type is defined by a Depfix-managed module:

```python
import depfix

jwt_old = depfix.import_module("PyJWT==2.10.0", module="jwt")
jwt_new = depfix.import_module("PyJWT==2.10.1", module="jwt")

jwk_data = {
    "kty": "oct",
    "k": "c2VjcmV0LXNlY3JldC1zZWNyZXQ",
    "alg": "HS256",
}
old_key = jwt_old.PyJWK.from_dict(jwk_data)

producer = depfix.realm_of(old_key)
assert producer is not None
assert producer.package == "pyjwt==2.10.0"
assert producer.module == "jwt.api_jwk"
```

`realm_of()` returns `None` for numbers, strings, standard-library values, application classes, and unmanaged package
objects. It also returns `None` when a package decorator generates an application-owned class: the class is owned by the
application module even if hidden package metadata inside it came from one version.

## Reject a foreign value early

Use `assert_same_realm()` at an explicit boundary:

```python
depfix.assert_same_realm(jwt_new, old_key)
```

This raises `RealmBoundaryError` with the producer and consumer package versions, both exact realm identities, and the
value path. Unmanaged application values pass through. Nested builtin dictionaries, lists, tuples, sets, and frozensets
are checked by default; pass `recursive=False` to check only top-level values.

Guard a function or method declaratively when the boundary is reused:

```python
@depfix.enforce_same_realm(jwt_new, parameters=("key",))
def encode(payload: dict[str, object], key: object) -> str:
    return jwt_new.encode(payload, key)
```

Named `parameters` avoid checking unrelated arguments that may legitimately belong to other managed packages. Omitting
`parameters` checks every supplied argument. The decorator preserves synchronous and asynchronous functions and can
check a direct return value with `check_return=True`.

## Convert through an application contract

Do not pass `old_key` to the new package. Retain or derive an application-owned representation, validate it, and let the
consumer construct its own object:

```python
new_key = jwt_new.PyJWK.from_dict(jwk_data)
depfix.assert_same_realm(jwt_new, new_key)
token = encode({"sub": "demo"}, new_key)
```

Use the narrowest documented representation for each boundary:

| Library-owned value | Application boundary | Receiving operation |
| --- | --- | --- |
| `packaging.Version` | normalized string | `receiving_version.Version(text)` |
| attrs instance | validated application DTO/dictionary | receiving application class constructor |
| `PyJWK` | validated JWK dictionary/JSON | `receiving_jwt.PyJWK.from_dict(data)` |
| urllib3 `Retry` | explicit retry settings | `receiving_urllib3.util.Retry(**settings)` |

Keep exception handling inside the producing version too, then raise an application-owned exception. Python exception
classes have the same nominal identity problem as other classes.

These helpers detect provenance; they do not prove semantic compatibility, translate values, inspect arbitrary object
graphs, or make pickle a safe format. Process workers strengthen runtime isolation but still require the same explicit,
validated serialization contract.
