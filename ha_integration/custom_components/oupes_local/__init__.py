"""Oupes local bridge integration — talks to our docker backend."""
from __future__ import annotations

from datetime import timedelta
import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_BASE_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
SCAN_INTERVAL = timedelta(seconds=5)


class OupesCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, base_url: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.base_url = base_url.rstrip("/")
        self.session: aiohttp.ClientSession = async_get_clientsession(hass)

    async def _async_update_data(self) -> dict:
        try:
            async with self.session.get(f"{self.base_url}/status", timeout=aiohttp.ClientTimeout(total=5)) as r:
                r.raise_for_status()
                return await r.json()
        except Exception as err:
            raise UpdateFailed(str(err)) from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = OupesCoordinator(hass, entry.data[CONF_BASE_URL])
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
