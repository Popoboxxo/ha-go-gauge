"""Go Gauge HA - sensor platform (direct API data).

Modell-Katalog = EIN Sensor ("Go Gauge Models") mit dem kompletten Katalog
als JSON-Attribute -> dynamisch, neue Modelle erscheinen automatisch ohne
neue Entitäten. Zusätzlich: Live-Anzahl, Günstigstes, Free-Modelle als
kompakte Lese-Sensoren.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    WINDOW_LABELS,
)
from .coordinator import (
    GoGaugeCoordinator,
    burn_rate_per_hour,
    forecast_percent,
    pace_status,
    remaining_percent,
    seconds_until_reset,
)
from .entity import GoGaugeAccountEntityBase, GoGaugeEntityBase

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create entities (fixed set - model list lives in ONE sensor's attributes).

    Katalog-Entities (Modelle, Live-Count, Guenstigstes, Free) nur von der
    ersten Instanz ('_catalog_owner') - weitere Workspace-Instanzen erzeugen
    KEINE Duplikate. Workspace-Sensoren kommen von jeder Instanz.
    """
    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for ws in coordinator.data.get("workspaces", []):
        key = ws["key"]
        for win in ("5h", "week", "month"):
            label = WINDOW_LABELS.get(win, win)
            entities.append(UsagePercentSensor(
                coordinator, entry, key=key, win=win, label=label))
            entities.append(ResetTimestampSensor(
                coordinator, entry, key=key, win=win, label=label))
            entities.append(UsageForecastSensor(
                coordinator, entry, key=key, win=win, label=label))
            entities.append(UsagePaceSensor(
                coordinator, entry, key=key, win=win, label=label))
            entities.append(RemainingBudgetSensor(
                coordinator, entry, key=key, win=win, label=label))
            entities.append(TimeUntilResetSensor(
                coordinator, entry, key=key, win=win, label=label))
            entities.append(BurnRateSensor(
                coordinator, entry, key=key, win=win, label=label))

    if getattr(coordinator, "is_catalog_owner", True):
        # Modell-Katalog: EIN Sensor mit JSON-Attributen (dynamisch)
        entities.append(ModelCatalogSensor(coordinator, entry))
        entities.append(LiveModelsCountSensor(coordinator, entry))
        entities.append(CheapestModelSensor(coordinator, entry))
        entities.append(FreeModelsSensor(coordinator, entry))

    async_add_entities(entities)


def _window_placeholders(label: str) -> dict[str, str]:
    """Translation placeholders for the per-window sensors (``{window}``)."""
    return {"window": label}


class UsagePercentSensor(GoGaugeEntityBase, SensorEntity):
    """Percent usage of one workspace window (5h/week/month).

    Fix 2026-09-06 (Live-Test HA 2026.9): Bei 'no_subscription'/'error'
    KEINE Status-Strings ('Kein Abo'/'Fehler') als State - ein
    MEASUREMENT/%-Sensor mit String-State wird von aktuellem HA beim
    Hinzufuegen hart abgelehnt (ValueError, Entity fehlt komplett).
    Stattdessen None (= unbekannt); der Status bleibt ueber Icon
    (shield-off) + Attribute (status/note) + Abo-/API-Binary-Sensoren
    sichtbar. Statistiken (%-Verlauf) bleiben so erhalten.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:speedometer"
    _attr_translation_key = "usage"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_percent"
        self._attr_translation_placeholders = _window_placeholders(label)

    def _status(self) -> str | None:
        ws = self._ws(self._key)
        return ws.get("status") if ws else None

    @property
    def native_value(self) -> float | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        if ws.get("status") in ("no_subscription", "error"):
            return None
        blk = (ws.get("windows") or {}).get(self._win) or {}
        pct = blk.get("percent")
        return float(pct) if isinstance(pct, (int, float)) else None

    @property
    def icon(self) -> str:
        """Rot bei Abo-Problem / rate-limit, sonst Tacho."""
        status = self._status()
        if status == "no_subscription":
            return "mdi:shield-off-outline"
        return "mdi:speedometer"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ws = self._ws(self._key)
        attrs: dict[str, Any] = {"workspace_key": self._key, "window": self._win}
        if ws:
            blk = (ws.get("windows") or {}).get(self._win) or {}
            reset = blk.get("resets_at")
            attrs.update({
                "status": ws.get("status"),
                "note": ws.get("note"),
                "resets_at_iso": reset.isoformat() if isinstance(reset, datetime) else None,
            })
        return attrs


class ResetTimestampSensor(GoGaugeEntityBase, SensorEntity):
    """Concrete reset time as HA timestamp entity."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-reset"
    _attr_translation_key = "reset"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_reset"
        self._attr_translation_placeholders = _window_placeholders(label)

    @property
    def native_value(self) -> datetime | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        blk = (ws.get("windows") or {}).get(self._win) or {}
        val = blk.get("resets_at")
        return val if isinstance(val, datetime) else None


class UsageForecastSensor(GoGaugeEntityBase, SensorEntity):
    """Linear projection of the window's usage percent onto the full window.

    forecast = percent / elapsed_fraction, elapsed_fraction = elapsed time
    since window start / nominal window length (WINDOW_SECONDS). Can exceed
    100% on purpose (Daniel-Feedback 2026-09-07) - that's the point, it
    shows whether current pace will blow the budget before the window ends.
    Right after a reset (elapsed_fraction ~ 0) the projection is undefined -
    same "None instead of a fake number" pattern as UsagePercentSensor.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:trending-up"
    _attr_translation_key = "forecast"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_forecast"
        self._attr_translation_placeholders = _window_placeholders(label)

    @property
    def native_value(self) -> float | None:
        return forecast_percent(self._ws(self._key), self._win)


class UsagePaceSensor(GoGaugeEntityBase, SensorEntity):
    """Ampel je Fenster: green/yellow/red je nach hochgerechnetem Prognose-%.

    Grenzen kommen aus zwei Number-Entities (einheitlich fuer alle Fenster):
    Warnschwelle = Gruen/Gelb-Grenze, Ampel-Rot-Grenze = Gelb/Rot-Grenze.
    """

    _attr_icon = "mdi:speedometer-medium"
    _attr_translation_key = "pace"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_pace"
        self._attr_translation_placeholders = _window_placeholders(label)

    @property
    def native_value(self) -> str | None:
        forecast = forecast_percent(self._ws(self._key), self._win)
        if forecast is None:
            return None
        return pace_status(forecast, self.coordinator.warn_percent,
                            self.coordinator.pace_red_percent)

    @property
    def icon(self) -> str:
        return {"green": "mdi:check-circle-outline",
                "yellow": "mdi:alert-circle-outline",
                "red": "mdi:close-circle-outline"}.get(self.native_value or "",
                                                       "mdi:speedometer-medium")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"forecast_percent": forecast_percent(self._ws(self._key), self._win),
                "green_below": self.coordinator.warn_percent,
                "red_above": self.coordinator.pace_red_percent}


class RemainingBudgetSensor(GoGaugeEntityBase, SensorEntity):
    """Restbudget je Fenster: 100 - genutzte Prozent.

    Inverse des Nutzungs-Sensors; None (nicht 100) bei fehlendem Abo/Fehler,
    damit ein Restbudget nie als scheinbar volle Reserve erscheint.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:gauge"
    _attr_translation_key = "remaining"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_remaining"
        self._attr_translation_placeholders = _window_placeholders(label)

    @property
    def native_value(self) -> float | None:
        return remaining_percent(self._ws(self._key), self._win)


class TimeUntilResetSensor(GoGaugeEntityBase, SensorEntity):
    """Restzeit bis zum Fenster-Reset in Stunden (DURATION-Entity)."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "h"
    _attr_icon = "mdi:timer-sand"
    _attr_translation_key = "time_to_reset"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_time_to_reset"
        self._attr_translation_placeholders = _window_placeholders(label)

    @property
    def native_value(self) -> float | None:
        seconds = seconds_until_reset(self._ws(self._key), self._win)
        return seconds / 3600.0 if seconds is not None else None


class BurnRateSensor(GoGaugeEntityBase, SensorEntity):
    """Verbrauchstempo je Fenster in %/h (Steigung der letzten 2h)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%/h"
    _attr_icon = "mdi:fire"
    _attr_translation_key = "burn_rate"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_burn_rate"
        self._attr_translation_placeholders = _window_placeholders(label)

    @property
    def native_value(self) -> float | None:
        # Defensive getattr: leichte Test-Doubles binden die Entities ohne
        # echten Coordinator (kein _usage_samples) - dann None statt Crash.
        samples = getattr(self.coordinator, "_usage_samples", {}) or {}
        return burn_rate_per_hour(samples.get(f"{self._key}:{self._win}", []))


class ModelCatalogSensor(GoGaugeAccountEntityBase, SensorEntity):
    """EIN Sensor fuer den kompletten Modell-Katalog (dynamisch via Attribute).

    State = Anzahl gelisteter Modelle. Attribute enthaelt das ganze Verzeichnis
    als JSON-String (`catalog`) plus Aufbereitungen (live/free/cheapest...).
    Neue Modelle erscheinen hier automatisch beim naechsten Modell-Refresh -
    ganz ohne neue Entitaeten.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:format-list-bulleted"
    _attr_translation_key = "model_catalog"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_model_catalog"
        # Cache fuer extra_state_attributes, invalidiert ueber
        # "models_updated_at" (aendert sich nur bei echtem Modell-Refresh,
        # nicht bei jedem Coordinator-Poll) - vermeidet dict-Copy +
        # json.dumps bei jedem Attribut-Zugriff (Audit 2026-09-04).
        self._attrs_cache_key: Any = None
        self._attrs_cache: dict[str, Any] | None = None

    @property
    def native_value(self) -> int | None:
        block = self.coordinator.data.get("models_block") or {}
        models = block.get("models") or []
        return len(models) if models else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        block_raw = self.coordinator.data.get("models_block") or {}
        cache_key = block_raw.get("models_updated_at")
        if (
            cache_key is not None
            and cache_key == self._attrs_cache_key
            and self._attrs_cache is not None
        ):
            return self._attrs_cache

        block = dict(block_raw)
        models = block.pop("models", [])
        attrs: dict[str, Any] = {
            "models_updated_at": block.get("models_updated_at"),
            "count": len(models),
            "live_count": sum(1 for m in models if m.get("live")),
            # Free/Günstigstes kommen aus dem Coordinator-Block: dort sind
            # bereits nicht mehr live gelistete Modelle rausgefiltert (Fix
            # 2026-09-05) - hier NICHT neu über die volle models-Liste
            # rechnen, sonst tauchen tote Modelle wieder auf.
            "free_models": block.get("free_models") or [],
            "cheapest_model": block.get("cheapest_model"),
            "cheapest_overall": block.get("cheapest_overall"),
            # Sortiert nach Kosten-Nutzen-Ratio (billigste zuerst); nur
            # live-gelistete Modelle (live:False = PRICING-Altlast ignoriert)
            "ranking_by_cost": [
                {"id": m["id"], "usd_per_1m_mixed": m.get("usd_per_1m_mixed"),
                 "month_req_per_usd": m.get("month_req_per_usd"),
                 "free": m.get("free"), "live": m.get("live")}
                for m in sorted(
                    [m for m in models
                     if m.get("usd_per_1m_mixed") is not None
                     and m.get("live") is not False],
                    key=lambda m: m["usd_per_1m_mixed"])
            ],
        }
        # Kompletter Katalog als JSON-String (Templates/LoV-freundlich)
        attrs["catalog_json"] = json.dumps(models, ensure_ascii=False)

        self._attrs_cache_key = cache_key
        self._attrs_cache = attrs
        return attrs


class LiveModelsCountSensor(GoGaugeAccountEntityBase, SensorEntity):
    """Live verfuegbare Modelle laut API."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:check-network-outline"
    _attr_translation_key = "live_models_count"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_models_live_count"

    @property
    def native_value(self) -> int | None:
        block = self.coordinator.data.get("models_block") or {}
        return block.get("model_count_live")


class CheapestModelSensor(GoGaugeAccountEntityBase, SensorEntity):
    """Guenstigstes bezahltes Modell nach gemischtem $/1M."""

    _attr_icon = "mdi:crown-outline"
    _attr_translation_key = "cheapest_model"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cheapest_model"

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data.get("models_block") or {}).get("cheapest_model")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        block = self.coordinator.data.get("models_block") or {}
        return {
            "cheapest_overall": block.get("cheapest_overall"),
            "ratio_usd_per_1m": block.get("cheapest_ratio"),
        }


class FreeModelsSensor(GoGaugeAccountEntityBase, SensorEntity):
    _attr_icon = "mdi:gift-outline"
    _attr_translation_key = "free_models"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_free_models"

    @property
    def native_value(self) -> str | None:
        free = (self.coordinator.data.get("models_block") or {}).get("free_models") or []
        return ", ".join(free) if free else None
