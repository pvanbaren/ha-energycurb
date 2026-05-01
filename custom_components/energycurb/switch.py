"""Switch platform — per-hub configuration toggles.

* Live Power Readings — power readings update at 1 Hz vs once per minute.
* Extra Electrical Sensors — exposes per-circuit current / power factor /
  reactive power and per-phase voltage / frequency entities.

Both toggles write into entry.options and rely on the existing
options-update listener to reload the integration. The Live Power
toggle additionally enqueues a hub-config push so the streamer picks up
the new sample_period_ms within ~1s instead of waiting up to 5 minutes
for its periodic config refresh.
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
    CONF_EXTRA_SENSORS,
    CONF_SAMPLE_PERIOD_S,
    DEFAULT_EXTRA_SENSORS,
    DEFAULT_SAMPLE_PERIOD_S,
    DOMAIN,
    SIGNAL_NEW_DEVICE,
)
from .http_server import CurbHttpServer, enqueue_hub_message

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
        async_add_entities(
            [
                CurbLivePowerSwitch(entry, serial),
                CurbExtraSensorsSwitch(entry, serial),
            ]
        )

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
        # Tell the hub to re-fetch hub-config on its next message
        # poll instead of waiting up to 5 minutes for the periodic
        # refresh. Without this, the hub's internal sampler can stay
        # on the old sample_period_ms cadence (so e.g. 1-minute mode
        # → live-mode keeps producing samples every 60s for up to a
        # few minutes, leaving power sensors looking frozen). Mirrors
        # what the options-flow Save button does.
        enqueue_hub_message(
            self.hass, self._entry, self._serial, {"type": "config"}
        )
        # async_update_entry triggers the options-update listener
        # registered in __init__.async_setup_entry, which reloads the
        # integration. The switch is recreated post-reload with is_on
        # derived from the saved options. The pending hub-message
        # queue lives in hass.data (not on the server instance), so
        # the enqueued config push above survives the reload and is
        # delivered when the hub's next /v3/messages poll arrives.
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_options
        )


class CurbExtraSensorsSwitch(SwitchEntity):
    """Toggle the per-circuit I/PF/VAR + per-phase V/Hz sensors.

    The hub already sends these fields on every samples POST, so this
    is purely an HA-side filter on which entities get created. Toggling
    triggers an integration reload so sensor.py's setup runs again with
    the new flag value.
    """

    _attr_has_entity_name = True
    _attr_name = "Extra Electrical Sensors"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, entry: ConfigEntry, serial: str) -> None:
        self._entry = entry
        self._serial = serial
        self._attr_unique_id = f"curb_{serial}_extra_sensors"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Curb {serial}",
            manufacturer="Curb",
            model="Energy Monitor",
        )

    @property
    def is_on(self) -> bool:
        devices = self._entry.options.get(CONF_DEVICES, {})
        return bool(
            devices.get(self._serial, {}).get(
                CONF_EXTRA_SENSORS, DEFAULT_EXTRA_SENSORS
            )
        )

    async def async_turn_on(self, **kwargs) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        self._set(False)

    def _set(self, enabled: bool) -> None:
        new_options = copy.deepcopy(dict(self._entry.options))
        devices = new_options.setdefault(CONF_DEVICES, {})
        devices.setdefault(self._serial, {})
        devices[self._serial][CONF_EXTRA_SENSORS] = enabled
        # Reload via the options-update listener — the new entities are
        # created on the next pass through sensor.async_setup_entry.
        # Disabled entities aren't auto-removed from the entity
        # registry; they'll show as `unavailable` until the user
        # deletes them.
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_options
        )
