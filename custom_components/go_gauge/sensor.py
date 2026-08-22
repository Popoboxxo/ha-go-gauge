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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL, WINDOW_LABELS
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create entities (fixed set - model list lives in ONE sensor's attributes)."""
    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for ws in coordinator.data.get("workspaces", []):
        key = ws["key"]
        slot = ws.get("token_slot", key)
        for win in ("5h", "week", "month"):
            label = WINDOW_LABELS.get(win, win)
            entities.append(UsagePercentSensor(coordinator, entry, key, slot, win, label))
            entities.append(ResetTimestampSensor(coordinator, entry, key, slot, win, label))

    # Modell-Katalog: EIN Sensor mit JSON-Attributen (dynamisch)
    entities.append(ModelCatalogSensor(coordinator, entry))
    entities.append(LiveModelsCountSensor(coordinator, entry))
    entities.append(CheapestModelSensor(coordinator, entry))
    entities.append(FreeModelsSensor(coordinator, entry))

    async_add_entities(entities)


def name_or_slot(slot) -> str:
    return f"WS {slot}"


class _GoGaugeEntity(CoordinatorEntity):
    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Go Gauge HA",
            "manufacturer": MANUFACTURER,
            "model": MODEL,
        }

    def _ws(self, key: str) -> dict[str, Any] | None:
        for ws in self.coordinator.data.get("workspaces", []):
            if ws["key"] == key:
                return ws
        return None


class UsagePercentSensor(_GoGaugeEntity, SensorEntity):
    """Percent usage of one workspace window (5h/week/month)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, entry, key: str, slot, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_percent"
        self._attr_name = f"Go Gauge {name_or_slot(slot)} {label} Nutzung"

    @property
    def native_value(self) -> float | None:
        ws = self._ws(self._key)
        if not ws or ws.get("status") not in ("ok", None):
            return None
        blk = (ws.get("windows") or {}).get(self._win) or {}
        pct = blk.get("percent")
        return float(pct) if isinstance(pct, (int, float)) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ws = self._ws(self._key)
        attrs: dict[str, Any] = {"workspace_key": self._key, "window": self._win}
        if ws:
            blk = (ws.get("windows") or {}).get(self._win) or {}
            reset = blk.get("resets_at")
            attrs.update({
                "status": blk.get("status"),
                "resets_at_iso": reset.isoformat() if isinstance(reset, datetime) else None,
            })
        return attrs


class ResetTimestampSensor(_GoGaugeEntity, SensorEntity):
    """Concrete reset time as HA timestamp entity."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-reset"

    def __init__(self, coordinator, entry, key: str, slot, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_reset"
        self._attr_name = f"Go Gauge {name_or_slot(slot)} {label} Reset"

    @property
    def native_value(self) -> datetime | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        blk = (ws.get("windows") or {}).get(self._win) or {}
        val = blk.get("resets_at")
        return val if isinstance(val, datetime) else None


class ModelCatalogSensor(_GoGaugeEntity, SensorEntity):
    """EIN Sensor fuer den kompletten Modell-Katalog (dynamisch via Attribute).

    State = Anzahl gelisteter Modelle. Attribute enthaelt das ganze Verzeichnis
    als JSON-String (`catalog`) plus Aufbereitungen (live/free/cheapest...).
    Neue Modelle erscheinen hier automatisch beim naechsten Modell-Refresh -
    ganz ohne neue Entitaeten.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_model_catalog"
        self._attr_name = "Go Gauge Modelle"

    @property
    def native_value(self) -> int | None:
        block = self.coordinator.data.get("models_block") or {}
        models = block.get("models") or []
        return len(models) if models else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        block = dict(self.coordinator.data.get("models_block") or {})
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
        return attrs


class LiveModelsCountSensor(_GoGaugeEntity, SensorEntity):
    """Live verfuegbare Modelle laut API."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:check-network-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_models_live_count"
        self._attr_name = "Go Gauge Live-Modelle"

    @property
    def native_value(self) -> int | None:
        block = self.coordinator.data.get("models_block") or {}
        return block.get("model_count_live")


class CheapestModelSensor(_GoGaugeEntity, SensorEntity):
    """Guenstigstes bezahltes Modell nach gemischtem $/1M."""

    _attr_icon = "mdi:crown-outline"

    def __init__(self, coordinator, entry) -> None:
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


class FreeModelsSensor(_GoGaugeEntity, SensorEntity):
    _attr_icon = "mdi:gift-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_free_models"
        self._attr_name = "Go Gauge Free-Modelle"

    @property
    def native_value(self) -> str | None:
        free = (self.coordinator.data.get("models_block") or {}).get("free_models") or []
        return ", ".join(free) if free else None
