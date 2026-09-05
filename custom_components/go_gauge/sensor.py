"""Go Gauge HA - sensor platform (direct API data).

Modell-Katalog = EIN Sensor ("Go Gauge Modelle") mit dem kompletten Katalog
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

from .const import DOMAIN, WINDOW_LABELS
from .coordinator import GoGaugeCoordinator
from .entity import GoGaugeEntityBase

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

    ws_name = getattr(coordinator, "ws_name", "") or "WS 1"

    for ws in coordinator.data.get("workspaces", []):
        key = ws["key"]
        for win in ("5h", "week", "month"):
            label = WINDOW_LABELS.get(win, win)
            entities.append(UsagePercentSensor(
                coordinator, entry, key=key, win=win, label=label, ws_name=ws_name))
            entities.append(ResetTimestampSensor(
                coordinator, entry, key=key, win=win, label=label, ws_name=ws_name))

    if getattr(coordinator, "is_catalog_owner", True):
        # Modell-Katalog: EIN Sensor mit JSON-Attributen (dynamisch)
        entities.append(ModelCatalogSensor(coordinator, entry))
        entities.append(LiveModelsCountSensor(coordinator, entry))
        entities.append(CheapestModelSensor(coordinator, entry))
        entities.append(FreeModelsSensor(coordinator, entry))

    async_add_entities(entities)


def _display_name(name: str) -> str:
    return name or "WS 1"


class UsagePercentSensor(GoGaugeEntityBase, SensorEntity):
    """Percent usage of one workspace window (5h/week/month).

    Bei 'no_subscription' (kein aktives Abo) zeigt der Sensor den Status
    direkt als State - nicht 'Unbekannt'.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str, label: str, ws_name: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_percent"
        self._attr_name = f"Go Gauge {_display_name(ws_name)} {label} Nutzung"

    def _status(self) -> str | None:
        ws = self._ws(self._key)
        return ws.get("status") if ws else None

    @property
    def native_value(self) -> float | str | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        status = ws.get("status")
        if status == "no_subscription":
            return "Kein Abo"
        if status == "error":
            return "Fehler"
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

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str, label: str, ws_name: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_reset"
        self._attr_name = f"Go Gauge {_display_name(ws_name)} {label} Reset"

    @property
    def native_value(self) -> datetime | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        blk = (ws.get("windows") or {}).get(self._win) or {}
        val = blk.get("resets_at")
        return val if isinstance(val, datetime) else None


class ModelCatalogSensor(GoGaugeEntityBase, SensorEntity):
    """EIN Sensor fuer den kompletten Modell-Katalog (dynamisch via Attribute).

    State = Anzahl gelisteter Modelle. Attribute enthaelt das ganze Verzeichnis
    als JSON-String (`catalog`) plus Aufbereitungen (live/free/cheapest...).
    Neue Modelle erscheinen hier automatisch beim naechsten Modell-Refresh -
    ganz ohne neue Entitaeten.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_model_catalog"
        self._attr_name = "Go Gauge Modelle"
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
        if cache_key is not None and cache_key == self._attrs_cache_key and self._attrs_cache is not None:
            return self._attrs_cache

        block = dict(block_raw)
        models = block.pop("models", [])
        attrs: dict[str, Any] = {
            "models_updated_at": block.get("models_updated_at"),
            "count": len(models),
            "live_count": sum(1 for m in models if m.get("live")),
            "free_models": [m["id"] for m in models if m.get("free")],
            "cheapest_model": block.get("cheapest_model"),
            "cheapest_overall": block.get("cheapest_overall"),
            # Sortiert nach Kosten-Nutzen-Ratio (billigste zuerst)
            "ranking_by_cost": [
                {"id": m["id"], "usd_per_1m_mixed": m.get("usd_per_1m_mixed"),
                 "month_req_per_usd": m.get("month_req_per_usd"), "free": m.get("free")}
                for m in sorted(
                    [m for m in models if m.get("usd_per_1m_mixed") is not None],
                    key=lambda m: m["usd_per_1m_mixed"])
            ],
        }
        # Kompletter Katalog als JSON-String (Templates/LoV-freundlich)
        attrs["catalog_json"] = json.dumps(models, ensure_ascii=False)

        self._attrs_cache_key = cache_key
        self._attrs_cache = attrs
        return attrs


class LiveModelsCountSensor(GoGaugeEntityBase, SensorEntity):
    """Live verfuegbare Modelle laut API."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:check-network-outline"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_models_live_count"
        self._attr_name = "Go Gauge Live-Modelle"

    @property
    def native_value(self) -> int | None:
        block = self.coordinator.data.get("models_block") or {}
        return block.get("model_count_live")


class CheapestModelSensor(GoGaugeEntityBase, SensorEntity):
    """Guenstigstes bezahltes Modell nach gemischtem $/1M."""

    _attr_icon = "mdi:crown-outline"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cheapest_model"
        self._attr_name = "Go Gauge Günstigstes Modell"

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


class FreeModelsSensor(GoGaugeEntityBase, SensorEntity):
    _attr_icon = "mdi:gift-outline"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_free_models"
        self._attr_name = "Go Gauge Free-Modelle"

    @property
    def native_value(self) -> str | None:
        free = (self.coordinator.data.get("models_block") or {}).get("free_models") or []
        return ", ".join(free) if free else None
