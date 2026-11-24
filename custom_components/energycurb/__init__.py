"""The Curb integration."""
from __future__ import annotations

import copy
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_CIRCUITS,
    CONF_CIRCUIT_CLAMP,
    CONF_CIRCUIT_VOLTAGE,
    CONF_DEVICES,
    CONF_HOST,
    CONF_PORT,
    DOMAIN,
    PLATFORMS,
)
from .http_server import CurbHttpServer

_LOGGER = logging.getLogger(__name__)

# A hub that has POSTed samples this recently is considered online and
# can't be removed — its next POST (every 1-60 s) would immediately
# re-create the device. Generous enough to cover a brief network blip.
DEVICE_REMOVE_OFFLINE_S = 5 * 60

# Pre-validator option values shipped in early 0.1.x. Translation keys
# must match [a-z0-9-_]+, so any stored options still carrying the old
# spellings get rewritten on load.
_LEGACY_OPTION_VALUES: dict[str, dict[str, str]] = {
    CONF_CIRCUIT_CLAMP:   {"100A": "100a", "50A": "50a", "30A": "30a"},
    CONF_CIRCUIT_VOLTAGE: {"110V": "110v", "220V": "220v"},
}


def _migrate_legacy_option_values(options: dict[str, Any]) -> bool:
    """Rewrite any circuit field still carrying a pre-validator value.
    Returns True if anything changed."""
    changed = False
    for device in options.get(CONF_DEVICES, {}).values():
        for circuit in device.get(CONF_CIRCUITS, []):
            for field, mapping in _LEGACY_OPTION_VALUES.items():
                if (new := mapping.get(circuit.get(field))) is not None:
                    circuit[field] = new
                    changed = True
    return changed


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Before the server or sensors touch anything, normalize any
    # pre-validator option values left behind in storage.
    migrated = copy.deepcopy(dict(entry.options))
    if _migrate_legacy_option_values(migrated):
        hass.config_entries.async_update_entry(entry, options=migrated)

    host: str = entry.data[CONF_HOST]
    port: int = int(entry.data[CONF_PORT])

    server = CurbHttpServer(hass, entry, host, port)
    try:
        await server.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Failed to bind {host}:{port} - {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = server

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    server: CurbHttpServer | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if server is not None:
        await server.async_stop()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow the user to delete a Curb hub device from the UI.

    Refused while the hub is still posting. Otherwise the hub's energy
    totals, cached readings, queued messages and per-hub options are
    all discarded; HA then removes the device and its entities.
    """
    serials = [
        ident for domain, ident in device_entry.identifiers if domain == DOMAIN
    ]
    if not serials:
        return True
    server: CurbHttpServer | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    for serial in serials:
        if server is not None and server.is_recently_seen(
            serial, DEVICE_REMOVE_OFFLINE_S
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_still_online",
                translation_placeholders={
                    "serial": serial,
                    "minutes": str(DEVICE_REMOVE_OFFLINE_S // 60),
                },
            )

    for serial in serials:
        if server is not None:
            await server.async_forget_device(serial)
        _LOGGER.info("Removed Curb hub %s", serial)

    devices = entry.options.get(CONF_DEVICES, {})
    if any(serial in devices for serial in serials):
        options = copy.deepcopy(dict(entry.options))
        for serial in serials:
            options[CONF_DEVICES].pop(serial, None)
        # Triggers the options-update reload; the server state was
        # already purged and saved above, so the reloaded server comes
        # up without this hub.
        hass.config_entries.async_update_entry(entry, options=options)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Options carry per-circuit names and clamp config; reloading is the
    # simplest way to rebuild sensor names and pick up the new hub-config.
    await hass.config_entries.async_reload(entry.entry_id)
