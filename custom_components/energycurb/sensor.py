"""Sensor platform — 18 power + 18 energy sensors per Curb hub."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CIRCUIT_NAME,
    DOMAIN,
    NUM_CIRCUITS,
    SIGNAL_NEW_DEVICE,
    SIGNAL_UPDATE_FMT,
)
from .hub_config import _default_circuit_name
from .http_server import EnergyCurbHttpServer

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    server: EnergyCurbHttpServer = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add_device(serial: str) -> None:
        entities: list[SensorEntity] = []
        for i in range(NUM_CIRCUITS):
            entities.append(CurbCircuitPowerSensor(server, serial, i))
            entities.append(CurbCircuitEnergySensor(server, serial, i))
        async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add_device)
    )

    for serial in list(server.serials):
        _add_device(serial)


class _CurbCircuitBase(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        server: EnergyCurbHttpServer,
        serial: str,
        circuit_idx: int,
    ) -> None:
        self._server = server
        self._serial = serial
        self._idx = circuit_idx
        circuits = server.circuits_for(serial)
        self._friendly = (
            circuits[circuit_idx].get(CONF_CIRCUIT_NAME)
            or _default_circuit_name(circuit_idx)
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Curb {serial}",
            manufacturer="Curb",
            model="Energy Monitor",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE_FMT.format(serial=self._serial),
                self.async_write_ha_state,
            )
        )


class CurbCircuitPowerSensor(_CurbCircuitBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        server: EnergyCurbHttpServer,
        serial: str,
        circuit_idx: int,
    ) -> None:
        super().__init__(server, serial, circuit_idx)
        # Unprefixed unique_id predates the energy sensor; keep it so
        # existing installs don't lose their entity_id / rename history.
        self._attr_unique_id = f"curb_{serial}_circuit_{circuit_idx + 1}"
        self._attr_name = self._friendly

    @property
    def native_value(self) -> float | None:
        return self._server.latest.get(self._serial, {}).get(self._idx)

    @property
    def available(self) -> bool:
        return self._idx in self._server.latest.get(self._serial, {})


class CurbCircuitEnergySensor(_CurbCircuitBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        server: EnergyCurbHttpServer,
        serial: str,
        circuit_idx: int,
    ) -> None:
        super().__init__(server, serial, circuit_idx)
        self._attr_unique_id = f"curb_{serial}_circuit_{circuit_idx + 1}_energy"
        self._attr_name = f"{self._friendly} Energy"

    @property
    def native_value(self) -> float | None:
        wh = self._server.energy_wh.get(self._serial, {}).get(self._idx)
        if wh is None:
            return None
        return wh / 1000.0

    @property
    def available(self) -> bool:
        return self._idx in self._server.energy_wh.get(self._serial, {})
