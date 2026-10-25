#!/usr/bin/env python3
"""Unit tests for the HA-free account rules (``account_logic.py``) + constants.

Covers the entity-identity helpers used by the one-time adoption sweep, the
evidence TTLs and the tri-state reachability matrix (plan §5), plus the account
constants/``conf_entry_kind`` defaulting added to ``const.py`` (plan §2.5).

NOTE: ``conftest.py`` installs the base HA module fakes; ``account_logic``
imports no HA code itself, so no extra stub block is needed here. Modules are
loaded via ``importlib`` under their real ``go_gauge.*`` names and in
dependency order (``const`` before ``account_logic``).
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


def _load(name: str, path: str):
    """Dynamically load an integration module under its package-qualified name."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
logic = _load("go_gauge.account_logic", str(BASE / "account_logic.py"))

NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)

# TTLs used by most reachability cases: models 2x60, usage 3x10.
MODELS_TTL = logic.models_ttl(60)  # 120 min
USAGE_TTL = logic.usage_ttl(10)  # 30 min


def _ev(source: str, outcome: str, minutes_ago: float, workspace: str | None = None):
    """Build a single Evidence entry relative to NOW."""
    return logic.Evidence(
        source=source,
        workspace=workspace,
        outcome=outcome,
        at=NOW - timedelta(minutes=minutes_ago),
    )


def _resolve(ev, models_ttl=MODELS_TTL, usage_ttl=USAGE_TTL):
    """Resolve reachability for ``ev`` at NOW with the given TTLs."""
    return logic.resolve_reachability(ev, NOW, models_ttl, usage_ttl)


class TestEntityIdentity:
    """unique_id helpers: canonical shape, legacy shape, platform matching."""

    def test_canonical_unique_id_all_kinds(self):
        """[PLAN2-M2] Canonical ids are ``account_<kind>`` for all five kinds."""
        expected = {
            "model_catalog": "account_model_catalog",
            "models_live_count": "account_models_live_count",
            "cheapest_model": "account_cheapest_model",
            "free_models": "account_free_models",
            "api_reachable": "account_api_reachable",
        }
        assert len(logic.ACCOUNT_ENTITY_KINDS) == 5
        for kind in logic.ACCOUNT_ENTITY_KINDS:
            assert logic.canonical_unique_id(kind) == expected[kind]

    def test_platform_by_kind_covers_all_kinds(self):
        """[PLAN2-M2] Every kind maps to exactly one platform (4 sensor + 1 binary)."""
        assert set(logic.PLATFORM_BY_KIND) == set(logic.ACCOUNT_ENTITY_KINDS)
        assert logic.PLATFORM_BY_KIND["api_reachable"] == "binary_sensor"
        assert list(logic.PLATFORM_BY_KIND.values()).count("sensor") == 4

    def test_kind_for_unique_id_canonical(self):
        """[PLAN2-M2] Canonical ids resolve back to their kind."""
        for kind in logic.ACCOUNT_ENTITY_KINDS:
            assert logic.kind_for_unique_id(logic.canonical_unique_id(kind)) == kind

    def test_kind_for_unique_id_legacy_workspace_prefix(self):
        """[PLAN2-M2] Legacy ``<workspace>_<kind>`` ids are recognised."""
        assert logic.kind_for_unique_id("ws1_model_catalog") == "model_catalog"
        assert logic.kind_for_unique_id("abc_models_live_count") == "models_live_count"
        assert logic.kind_for_unique_id("e2e_cheapest_model") == "cheapest_model"
        assert logic.kind_for_unique_id("e2e_free_models") == "free_models"
        assert logic.kind_for_unique_id("e2e_api_reachable") == "api_reachable"

    def test_kind_for_unique_id_longest_kind_first(self):
        """[PLAN2-M2] Suffix candidates are matched longest-first, deterministically."""
        lengths = [len(kind) for kind in logic._KINDS_LONGEST_FIRST]
        assert lengths == sorted(lengths, reverse=True)
        # "models_live_count" is the longest kind - its suffix must win over the
        # shorter "free_models"/"model_catalog" candidates.
        assert (
            logic.kind_for_unique_id("ws1_models_live_count") == "models_live_count"
        )

    def test_kind_for_unique_id_foreign_and_settings(self):
        """[PLAN2-M2] Foreign ids and the account *settings* ids yield None."""
        assert logic.kind_for_unique_id("ws1_usage") is None
        assert logic.kind_for_unique_id("go_gauge_1234abcd") is None
        assert logic.kind_for_unique_id("ws1_models") is None
        assert logic.kind_for_unique_id("ws1_subscription_active") is None
        # the legacy shape is "<anything>_<kind>", so a foreign *prefix* is fine
        assert logic.kind_for_unique_id("other_domain_model_catalog") == "model_catalog"
        # Settings ids of the account entry are controls, not catalog entities.
        assert logic.kind_for_unique_id("account_auto_update_models") is None
        assert logic.kind_for_unique_id("account_models_refresh_minutes") is None

    def test_kind_for_unique_id_empty_input(self):
        """[PLAN2-M2] Empty string / None input yields None (no crash)."""
        assert logic.kind_for_unique_id("") is None
        assert logic.kind_for_unique_id(None) is None

    def test_matches_kind_platform_and_uid(self):
        """[PLAN2-M2] matches_kind needs the platform AND the unique_id to agree."""
        assert logic.matches_kind(
            "sensor", "account_model_catalog", "model_catalog"
        )
        assert logic.matches_kind(
            "binary_sensor", "account_api_reachable", "api_reachable"
        )
        # legacy uid still matches
        assert logic.matches_kind("sensor", "ws1_model_catalog", "model_catalog")
        # right uid, wrong platform
        assert not logic.matches_kind("sensor", "account_api_reachable", "api_reachable")
        # right platform, uid belongs to another kind
        assert not logic.matches_kind("sensor", "account_free_models", "model_catalog")
        # unknown kind never matches
        assert not logic.matches_kind("sensor", "account_model_catalog", "nope")


class TestTtl:
    """Evidence TTLs derived from the configured intervals."""

    def test_models_ttl_default_interval(self):
        """[PLAN2-E5] 60 min base -> 120 min models TTL."""
        assert logic.models_ttl(60) == timedelta(minutes=120)

    def test_models_ttl_follows_base_interval(self):
        """[PLAN2-E5] TTL scales with the base interval (30 -> 60 min)."""
        assert logic.models_ttl(30) == timedelta(minutes=60)
        assert logic.models_ttl(1440) == timedelta(minutes=2880)

    def test_models_ttl_fallback_when_auto_refresh_off(self):
        """[PLAN2-E5] None (auto-refresh off) -> 120 min fallback."""
        assert logic.models_ttl(None) == timedelta(
            minutes=const.MODELS_TTL_FALLBACK_MINUTES
        )
        assert logic.models_ttl(None) == timedelta(minutes=120)

    def test_usage_ttl(self):
        """[PLAN2-E5] 10 min usage interval -> 30 min usage TTL."""
        assert logic.usage_ttl(10) == timedelta(minutes=30)
        assert logic.usage_ttl(5) == timedelta(minutes=15)


class TestReachabilityMatrix:
    """The four plan §5 rows, stale handling and the boundary case."""

    def test_matrix_fresh_ok_is_on(self):
        """[PLAN2-E5] Row 1: any fresh ok -> True / fresh_ok."""
        result = _resolve([_ev("models", "ok", 10)])
        assert result.state is True
        assert result.reason == "fresh_ok"
        assert result.counts == {"ok": 1, "neutral": 0, "fail": 0, "stale": 0}

    def test_matrix_ok_wins_over_fail_and_neutral(self):
        """[PLAN2-E5] Priority: a single fresh ok outranks any fresh fail/neutral."""
        result = _resolve(
            [
                _ev("usage", "fail", 5, workspace="ws1"),
                _ev("usage", "neutral", 4, workspace="ws1"),
                _ev("models", "ok", 3),
            ]
        )
        assert result.state is True
        assert result.reason == "fresh_ok"
        assert result.counts == {"ok": 1, "neutral": 1, "fail": 1, "stale": 0}

    def test_matrix_neutral_without_ok_is_unknown(self):
        """[PLAN2-E5] Row 2: no ok but a fresh neutral -> None / answered_no_success."""
        result = _resolve(
            [
                _ev("usage", "neutral", 5, workspace="ws1"),
                _ev("usage", "fail", 6, workspace="ws2"),
            ]
        )
        assert result.state is None
        assert result.reason == "answered_no_success"
        assert result.counts == {"ok": 0, "neutral": 1, "fail": 1, "stale": 0}

    def test_matrix_only_fail_is_off(self):
        """[PLAN2-E5] Row 3: only fresh failures -> False / all_fresh_failed."""
        result = _resolve(
            [
                _ev("models", "fail", 10),
                _ev("usage", "fail", 20, workspace="ws1"),
            ]
        )
        assert result.state is False
        assert result.reason == "all_fresh_failed"
        assert result.counts == {"ok": 0, "neutral": 0, "fail": 2, "stale": 0}

    def test_matrix_no_evidence_is_unknown(self):
        """[PLAN2-E5] Row 4: only stale evidence -> None / no_evidence."""
        result = _resolve([_ev("models", "fail", 500)])
        assert result.state is None
        assert result.reason == "no_evidence"
        assert result.counts == {"ok": 0, "neutral": 0, "fail": 0, "stale": 1}

    def test_matrix_empty_sequence_is_unknown(self):
        """[PLAN2-E5] Row 4: an empty sequence resolves to unknown, not False."""
        result = _resolve([])
        assert result.state is None
        assert result.reason == "no_evidence"
        assert result.counts == {"ok": 0, "neutral": 0, "fail": 0, "stale": 0}

    def test_stale_evidence_is_ignored_not_failed(self):
        """[PLAN2-E5] Stale evidence is dropped, never reinterpreted as fail."""
        result = _resolve(
            [
                _ev("models", "fail", 121),  # 1 min past the 120 min models TTL
                _ev("models", "ok", 10),
            ]
        )
        assert result.state is True
        assert result.counts["fail"] == 0
        assert result.counts["stale"] == 1

    def test_stale_ok_does_not_count(self):
        """[PLAN2-E5] A stale ok cannot keep the sensor ON - only fail is left."""
        result = _resolve(
            [
                _ev("models", "ok", 130),  # stale
                _ev("models", "fail", 20),  # fresh
            ]
        )
        assert result.state is False
        assert result.reason == "all_fresh_failed"
        assert result.counts["ok"] == 0
        assert result.counts["stale"] == 1

    def test_boundary_age_equals_ttl_is_fresh(self):
        """[PLAN2-E5] age == ttl counts as fresh (inclusive boundary)."""
        result = _resolve([_ev("models", "ok", 120)])
        assert result.state is True
        assert result.reason == "fresh_ok"
        assert result.counts["stale"] == 0

    def test_boundary_usage_age_equals_ttl_is_fresh(self):
        """[PLAN2-E5] The inclusive boundary holds for the usage TTL too."""
        result = _resolve([_ev("usage", "ok", 30, workspace="ws1")])
        assert result.state is True
        assert result.counts["stale"] == 0

    def test_mixed_sources_use_their_own_ttl(self):
        """[PLAN2-E5] Each source is aged against its own TTL, not a shared one."""
        result = _resolve(
            [
                # models: 60 min old, models TTL 120 -> fresh
                _ev("models", "fail", 60),
                # usage: 45 min old, usage TTL 30 -> stale
                _ev("usage", "ok", 45, workspace="ws1"),
            ]
        )
        assert result.state is False
        assert result.reason == "all_fresh_failed"
        assert result.counts == {"ok": 0, "neutral": 0, "fail": 1, "stale": 1}

    def test_mixed_sources_fresh_usage_ok_proves_reachable(self):
        """[PLAN2-E5] Fresh usage ok alone is enough for ON (models stale)."""
        result = _resolve(
            [
                _ev("models", "fail", 200),  # stale
                _ev("usage", "ok", 20, workspace="ws1"),
            ]
        )
        assert result.state is True
        assert result.counts == {"ok": 1, "neutral": 0, "fail": 0, "stale": 1}

    def test_models_ttl_uses_base_interval_not_backoff(self):
        """[PLAN2-E5] TTL derives from the base interval, so 10 min -> 20 min TTL."""
        base_ttl = logic.models_ttl(10)
        assert base_ttl == timedelta(minutes=20)
        # 25 min old evidence is stale at 2x10 (backoff would not extend it).
        result = _resolve([_ev("models", "fail", 25)], models_ttl=base_ttl)
        assert result.reason == "no_evidence"
        assert result.counts["stale"] == 1

    def test_unknown_outcome_is_neither_success_nor_failure(self):
        """[PLAN2-E5] A bogus outcome cannot prove success or failure."""
        result = _resolve([_ev("models", "bogus", 5)])
        assert result.state is None
        assert result.reason == "no_evidence"
        assert result.counts == {"ok": 0, "neutral": 0, "fail": 0, "stale": 0}

    def test_naive_timestamps_are_treated_as_utc(self):
        """[PLAN2-E5] Naive datetimes do not crash and are assumed UTC."""
        naive_now = NOW.replace(tzinfo=None)
        naive_ev = logic.Evidence(
            source="models",
            workspace=None,
            outcome="ok",
            at=naive_now - timedelta(minutes=5),
        )
        result = logic.resolve_reachability(
            [naive_ev], naive_now, MODELS_TTL, USAGE_TTL
        )
        assert result.state is True
        assert result.reason == "fresh_ok"

    def test_mixed_naive_and_aware_timestamps(self):
        """[PLAN2-E5] Aware now + naive evidence timestamp resolves instead of raising."""
        result = _resolve(
            [
                logic.Evidence(
                    source="models",
                    workspace=None,
                    outcome="ok",
                    at=NOW.replace(tzinfo=None) - timedelta(minutes=5),
                )
            ]
        )
        assert result.state is True

    def test_evidence_and_reachability_are_frozen(self):
        """[PLAN2-E5] Both value objects are immutable snapshots."""
        ev = _ev("models", "ok", 1)
        with_err = None
        try:
            ev.outcome = "fail"  # type: ignore[misc]
        except Exception as err:  # dataclasses.FrozenInstanceError
            with_err = err
        assert with_err is not None
        result = _resolve([ev])
        try:
            result.reason = "x"  # type: ignore[misc]
        except Exception as err:  # dataclasses.FrozenInstanceError
            with_err = err
        assert with_err is not None


class TestConstAdditions:
    """Account constants and the ``conf_entry_kind`` defaulting rules."""

    def test_conf_entry_kind_defaults_to_workspace(self):
        """[PLAN2-M2] Pre-2.0.0 entries (no ``entry_kind``) count as workspace."""
        assert const.conf_entry_kind({}) == const.ENTRY_KIND_WORKSPACE
        assert const.conf_entry_kind({}) == "workspace"
        assert const.conf_entry_kind({const.CONF_WORKSPACE_NAME: "e2e"}) == "workspace"

    def test_conf_entry_kind_explicit_values(self):
        """[PLAN2-M2] Explicit kinds round-trip."""
        assert (
            const.conf_entry_kind({const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT})
            == "account"
        )
        assert (
            const.conf_entry_kind({const.CONF_ENTRY_KIND: const.ENTRY_KIND_WORKSPACE})
            == "workspace"
        )

    def test_conf_entry_kind_is_defensive(self):
        """[PLAN2-M2] None / empty / non-string / non-mapping all yield the default."""
        assert const.conf_entry_kind({const.CONF_ENTRY_KIND: None}) == "workspace"
        assert const.conf_entry_kind({const.CONF_ENTRY_KIND: ""}) == "workspace"
        assert const.conf_entry_kind({const.CONF_ENTRY_KIND: 1}) == "workspace"
        assert const.conf_entry_kind(None) == "workspace"  # type: ignore[arg-type]
        assert isinstance(const.conf_entry_kind(None), str)  # type: ignore[arg-type]

    def test_account_platforms_exact(self):
        """[PLAN2-M2] ACCOUNT_PLATFORMS keeps the required order and membership."""
        assert const.ACCOUNT_PLATFORMS == [
            "sensor",
            "binary_sensor",
            "button",
            "switch",
            "number",
        ]

    def test_account_identity_and_settings_constants(self):
        """[PLAN2-M2] Account entry ids, data keys and settings defaults."""
        assert const.ACCOUNT_ENTRY_UNIQUE_ID == "go_gauge_account"
        assert const.ACCOUNT_DEVICE_IDENTIFIER == "account"
        assert const.DATA_ACCOUNT_KEY == "_account"
        assert const.DATA_ADOPTION_LOCK == "_adoption_lock"
        assert const.ACCOUNT_PUBLISH_TICK_MINUTES == 5
        assert const.ENTITY_NAME_ACCOUNT == "Go Gauge Konto"
        assert const.DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS is True
        assert const.DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES == 60
        assert const.ACCOUNT_MODELS_REFRESH_MIN == 1
        assert const.ACCOUNT_MODELS_REFRESH_MAX == 1440
        assert const.ACCOUNT_BACKOFF_MAX_MINUTES == 60

    def test_ttl_constants(self):
        """[PLAN2-E5] TTL factors and the 120 min fallback."""
        assert const.MODELS_TTL_FACTOR == 2
        assert const.USAGE_TTL_FACTOR == 3
        assert const.MODELS_TTL_FALLBACK_MINUTES == 120

    def test_legacy_settings_keys_unchanged(self):
        """[PLAN2-M2] Legacy option keys survive as account settings keys."""
        assert const.CONF_AUTO_UPDATE_MODELS == "auto_update_models"
        assert const.CONF_MODELS_REFRESH_MINUTES == "models_refresh_minutes"
