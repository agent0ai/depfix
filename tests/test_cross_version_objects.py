"""Characterize library-defined objects crossing real package-version realms.

These are deliberately selected risk probes, not a representative ecosystem
sample. Each case uses immutable published releases and a public object API.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import depfix
from depfix.manager import reset_runtime_state
from depfix.settings import reset_configuration

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("DEPFIX_RUN_LIVE_TESTS") != "1",
        reason="set DEPFIX_RUN_LIVE_TESTS=1 to exercise published PyPI artifacts",
    ),
]


@pytest.fixture(autouse=True)
def _clean_runtime_state():
    reset_configuration()
    reset_runtime_state()
    yield
    reset_runtime_state()
    reset_configuration()


def _configure(tmp_path: Path) -> None:
    depfix.configure(cache_dir=tmp_path / "cache", log_level="WARNING")


def test_packaging_version_comparison_is_silently_wrong_or_loud_across_realms(tmp_path: Path) -> None:
    _configure(tmp_path)
    legacy = depfix.import_module("packaging==21.3", module="packaging.version")
    current = depfix.import_module("packaging==24.2", module="packaging.version")

    legacy_version = legacy.Version("1.0")
    current_version = current.Version("1.0")

    assert legacy.Version("1.0") == legacy_version
    assert current.Version("1.0") == current_version
    assert legacy_version != current_version
    with pytest.raises(TypeError, match="not supported"):
        _ = legacy_version < current_version

    # A documented primitive representation is a safe application boundary.
    assert legacy.Version(str(current_version)) == legacy_version
    assert current.Version(str(legacy_version)) == current_version


def test_attrs_evolve_exposes_a_real_attribute_layout_change(tmp_path: Path) -> None:
    _configure(tmp_path)
    legacy = depfix.import_module("attrs==21.4.0", module="attrs")
    current = depfix.import_module("attrs==24.2.0", module="attrs")

    @legacy.define
    class LegacyRecord:
        value: int

    record = LegacyRecord(1)
    assert legacy.evolve(record, value=2).value == 2
    # The application owns this generated class, so provenance cannot be inferred.
    assert depfix.realm_of(record) is None
    assert not hasattr(legacy.fields(LegacyRecord).value, "alias")
    with pytest.raises(AttributeError, match="alias"):
        current.evolve(record, value=2)

    @current.define
    class CurrentRecord:
        value: int

    # Compatibility is directional: the old consumer does not read the new field.
    current_record = CurrentRecord(1)
    assert legacy.evolve(current_record, value=2).value == 2

    # Reconstructing from an agreed primitive shape avoids sharing attrs internals.
    converted = CurrentRecord(**legacy.asdict(record))
    assert current.evolve(converted, value=2).value == 2


def test_pyjwt_patch_versions_reject_each_others_jwk_identity(tmp_path: Path) -> None:
    _configure(tmp_path)
    legacy = depfix.import_module("PyJWT==2.10.0", module="jwt")
    current = depfix.import_module("PyJWT==2.10.1", module="jwt")
    jwk_data = {
        "kty": "oct",
        "k": "c2VjcmV0LXNlY3JldC1zZWNyZXQ",
        "alg": "HS256",
    }

    legacy_key = legacy.PyJWK.from_dict(jwk_data)
    current_key = current.PyJWK.from_dict(jwk_data)
    assert legacy.encode({"sub": "demo"}, legacy_key)
    assert current.encode({"sub": "demo"}, current_key)
    legacy_key_info = depfix.realm_of(legacy_key)
    assert legacy_key_info is not None and legacy_key_info.package == "pyjwt==2.10.0"
    with pytest.raises(depfix.RealmBoundaryError, match="pyjwt==2.10.0"):
        depfix.assert_same_realm(current, legacy_key)
    assert not isinstance(legacy_key, current.PyJWK)
    with pytest.raises(TypeError, match="Expected a string value"):
        current.encode({"sub": "demo"}, legacy_key)

    assert current.encode({"sub": "demo"}, current.PyJWK.from_dict(jwk_data))


def test_urllib3_retry_crossing_creates_a_delayed_invariant_failure(tmp_path: Path) -> None:
    _configure(tmp_path)
    legacy = depfix.import_module("urllib3==2.0.7", module="urllib3")
    current = depfix.import_module("urllib3==2.2.3", module="urllib3")

    legacy_retry = legacy.util.Retry(total=1)
    assert legacy.util.Retry.from_int(legacy_retry) is legacy_retry

    malformed = current.util.Retry.from_int(legacy_retry)
    assert malformed is not legacy_retry
    assert malformed.total is legacy_retry
    with pytest.raises(TypeError, match="unsupported operand type"):
        malformed.increment(error=OSError("network failure"))

    converted = current.util.Retry(total=legacy_retry.total)
    assert converted.increment(error=OSError("network failure")).total == 0
