"""Sensor entities for the Oupes local bridge."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OupesCoordinator
from .const import DOMAIN, SENSORS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coord: OupesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for key, spec in SENSORS.items():
        entities.append(OupesSensor(coord, entry, key, spec))
    # Derived: battery temp in Celsius from F*10.
    entities.append(BatteryTempC(coord, entry))
    add(entities)


class OupesSensor(CoordinatorEntity[OupesCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coord: OupesCoordinator, entry: ConfigEntry, key: str, spec: dict) -> None:
        super().__init__(coord)
        self._key = key
        self._attr_name = spec["name"]
        self._attr_native_unit_of_measurement = spec.get("unit")
        dc = spec.get("device_class")
        if dc:
            self._attr_device_class = SensorDeviceClass(dc)
        sc = spec.get("state_class")
        if sc:
            self._attr_state_class = SensorStateClass(sc)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._entry_id = entry.entry_id

    @property
    def native_value(self):
        attrs = (self.coordinator.data or {}).get("attrs") or {}
        return attrs.get(self._key)

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
            "sw_version": dev.get("sv"),
            "hw_version": dev.get("hv"),
            "connections": {("mac", dev.get("mac"))} if dev.get("mac") else set(),
        }


class BatteryTempC(CoordinatorEntity[OupesCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Battery Temperature"
    _attr_native_unit_of_measurement = "°C"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coord: OupesCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{entry.entry_id}_battery_temp_c"
        self._entry_id = entry.entry_id

    @property
    def native_value(self):
        v = ((self.coordinator.data or {}).get("attrs") or {}).get("battery_temp_f_x10")
        if v is None:
            return None
        f = v / 10
        return round((f - 32) * 5 / 9, 1)

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
