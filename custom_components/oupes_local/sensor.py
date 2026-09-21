"""Sensor entities for the Oupes local bridge."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OupesCoordinator
from .const import DOMAIN, EXPANSION_SENSORS, SENSORS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coord: OupesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for key, spec in SENSORS.items():
        entities.append(OupesSensor(coord, entry, key, spec))
    # Derived: battery temp in Celsius from F*10.
    entities.append(TempC(coord, entry, "battery_temp_f_x10", "Battery Temperature", "battery_temp_c"))
    # An expansion pack is decided once, from the first poll. Bolting a B2 on
    # to a running system therefore needs a reload of the config entry before
    # its entities appear -- which is the right trade against permanently
    # unavailable entities on every install that never had one.
    if (coord.data or {}).get("expansion"):
        for key, spec in EXPANSION_SENSORS.items():
            entities.append(OupesSensor(coord, entry, key, spec))
        entities.append(
            TempC(
                coord,
                entry,
                "expansion_battery_temp_f_x10",
                "Expansion Battery Temperature",
                "expansion_battery_temp_c",
            )
        )
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
        # The coordinator can be polling happily while the backend has lost the
        # device, so both have to hold: fresh poll AND a live TCP session.
        return super().available and bool((self.coordinator.data or {}).get("connected"))

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


class TempC(CoordinatorEntity[OupesCoordinator], SensorEntity):
    """A tenths-of-Fahrenheit attribute republished in Celsius."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "°C"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coord: OupesCoordinator, entry: ConfigEntry, key: str, name: str, unique: str
    ) -> None:
        super().__init__(coord)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique}"
        self._entry_id = entry.entry_id

    @property
    def native_value(self):
        v = ((self.coordinator.data or {}).get("attrs") or {}).get(self._key)
        if v is None:
            return None
        f = v / 10
        return round((f - 32) * 5 / 9, 1)

    @property
    def available(self) -> bool:
        # The coordinator can be polling happily while the backend has lost the
        # device, so both have to hold: fresh poll AND a live TCP session.
        return super().available and bool((self.coordinator.data or {}).get("connected"))

    @property
    def device_info(self) -> dict:
        dev = (self.coordinator.data or {}).get("device") or {}
        return {
            "identifiers": {(DOMAIN, dev.get("did") or self._entry_id)},
            "manufacturer": "Oupes",
            "model": dev.get("model") or "Unknown",
        }
