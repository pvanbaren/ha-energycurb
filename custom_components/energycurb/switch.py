"""Switch platform — Live Power Readings toggle per Curb hub.

ON  → power readings update at 1 Hz from the raw samples stream.
OFF → power readings update once per minute from the 1-minute aggregate.

Mirrors the 'Power update interval' dropdown in the options flow:
toggling the switch writes the same `sample_period_s` field
(`1` or `60`) into entry.options that the dropdown does, so either
surface stays in sync with the other.
"""
from __future__ import annotations

import copy
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEVICES,
    CONF_SAMPLE_PERIOD_S,
    DEFAULT_SAMPLE_PERIOD_S,
    DOMAIN,
    SIGNAL_NEW_DEVICE,
)
from .http_server import CurbHttpServer

_LOGGER = logging.getLogger(__name__)

# Period values written into entry.options on toggle. These match the
# two options in the config_flow's SelectSelector dropdown.
_PERIOD_LIVE = 1
_PERIOD_PER_MINUTE = 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    server: CurbHttpServer = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add_device(serial: str) -> None:
        async_add_entities([CurbLivePowerSwitch(entry, serial)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add_device)
    )

    for serial in list(server.serials):
        _add_device(serial)


class CurbLivePowerSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Live Power Readings"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:speedometer"

    def __init__(self, entry: ConfigEntry, serial: str) -> None:
        self._entry = entry
        self._serial = serial
        self._attr_unique_id = f"curb_{serial}_live_power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Curb {serial}",
            manufacturer="Curb",
            model="Energy Monitor",
        )

    @property
    def is_on(self) -> bool:
        devices = self._entry.options.get(CONF_DEVICES, {})
        val = devices.get(self._serial, {}).get(
            CONF_SAMPLE_PERIOD_S, DEFAULT_SAMPLE_PERIOD_S
        )
        try:
            return int(round(float(val))) == _PERIOD_LIVE
        except (TypeError, ValueError):
            return DEFAULT_SAMPLE_PERIOD_S == _PERIOD_LIVE

    async def async_turn_on(self, **kwargs) -> None:
        self._set_period(_PERIOD_LIVE)

    async def async_turn_off(self, **kwargs) -> None:
        self._set_period(_PERIOD_PER_MINUTE)

    def _set_period(self, period_s: int) -> None:
        new_options = copy.deepcopy(dict(self._entry.options))
        devices = new_options.setdefault(CONF_DEVICES, {})
        devices.setdefault(self._serial, {})
        devices[self._serial][CONF_SAMPLE_PERIOD_S] = period_s
        # async_update_entry triggers the options-update listener
        # registered in __init__.async_setup_entry, which reloads the
        # integration. The switch is recreated post-reload with is_on
        # derived from the saved options, so no explicit state push
        # needed here.
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_options
        )
