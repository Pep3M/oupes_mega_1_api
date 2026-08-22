"""Binary sensor entities."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OupesCoordinator
from .const import BINARY_SENSORS, DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coord: OupesCoordinator = hass.data[DOMAIN][entry.entry_id]
    add(OupesBinary(coord, entry, key, spec) for key, spec in BINARY_SENSORS.items())


class OupesBinary(CoordinatorEntity[OupesCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord: OupesCoordinator, entry: ConfigEntry, key: str, spec: dict) -> None:
        super().__init__(coord)
        self._key = key
        self._attr_name = spec["name"]
        dc = spec.get("device_class")
        if dc:
            self._attr_device_class = BinarySensorDeviceClass(dc)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._entry_id = entry.entry_id

    @property
    def is_on(self) -> bool | None:
        v = ((self.coordinator.data or {}).get("attrs") or {}).get(self._key)
        if v is None:
            return None
        return bool(v)

    @property
    def available(self) -> bool:
        return bool((self.coordinator.data or {}).get("connected"))

    @property
    def device_info(self) -> dict:
        dev = (self.coordinator.data or {}).get("device") or {}
        return {
            "identifiers": {(DOMAIN, dev.get("did") or self._entry_id)},
            "manufacturer": "Oupes",
            "model": dev.get("model") or "Unknown",
        }
