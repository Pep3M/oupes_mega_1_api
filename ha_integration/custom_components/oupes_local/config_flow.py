"""Config flow: ask the user where the backend lives."""
from __future__ import annotations

import voluptuous as vol
import aiohttp
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN


class OupesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            base = user_input[CONF_BASE_URL].rstrip("/")
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(f"{base}/status", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    r.raise_for_status()
                    snap = await r.json()
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                dev = (snap.get("device") or {})
                unique = dev.get("did") or dev.get("mac") or base
                await self.async_set_unique_id(unique)
                self._abort_if_unique_id_configured()
                title = dev.get("model") or "Oupes"
                return self.async_create_entry(title=title, data={CONF_BASE_URL: base})

        schema = vol.Schema({vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
