"""The EnergyCurb integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_HOST, CONF_PORT, DOMAIN, PLATFORMS
from .http_server import EnergyCurbHttpServer

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host: str = entry.data[CONF_HOST]
    port: int = int(entry.data[CONF_PORT])

    server = EnergyCurbHttpServer(hass, entry, host, port)
    try:
        await server.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Failed to bind {host}:{port} — {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = server

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    server: EnergyCurbHttpServer | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if server is not None:
        await server.async_stop()
    return unload_ok
