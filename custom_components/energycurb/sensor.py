"""Sensor platform — power + energy (+ production for bidirectional) sensors per Curb hub circuit.

Per-phase voltage and frequency are always exposed (one per chip with
a real voltage reference). Per-circuit current, power factor, and
reactive power are each gated on their own checkbox in the options
flow — one flag per quantity rather than a single all-or-nothing
toggle, so users who only care about (e.g.) current can opt in
without tripling the entity count.
"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CIRCUIT_BIDIRECTIONAL,
    CONF_CIRCUIT_NAME,
    DOMAIN,
    SIGNAL_NEW_DEVICE,
    SIGNAL_UPDATE_FMT,
)
from .hub_config import _default_circuit_name
from .http_server import CurbHttpServer

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    server: CurbHttpServer = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add_device(serial: str) -> None:
        entities: list[SensorEntity] = []
        circuits = server.circuits_for(serial)
        show_current = server.show_current(serial)
        show_power_factor = server.show_power_factor(serial)
        show_reactive_power = server.show_reactive_power(serial)
        for i in range(server.num_circuits_for(serial)):
            entities.append(CurbCircuitPowerSensor(server, serial, i))
            entities.append(CurbCircuitEnergySensor(server, serial, i))
            if circuits[i].get(CONF_CIRCUIT_BIDIRECTIONAL):
                entities.append(
                    CurbCircuitEnergyProductionSensor(server, serial, i)
                )
            if show_current:
                entities.append(CurbCircuitCurrentSensor(server, serial, i))
            if show_power_factor:
                entities.append(
                    CurbCircuitPowerFactorSensor(server, serial, i)
                )
            if show_reactive_power:
                entities.append(
                    CurbCircuitReactivePowerSensor(server, serial, i)
                )
        # Voltage and frequency are always-on — one per chip that has a
        # real voltage reference (filters out floating-pin chips).
        for chip_idx in server.voltage_chip_indices_for(serial):
            entities.append(CurbHubVoltageSensor(server, serial, chip_idx))
            entities.append(CurbHubFrequencySensor(server, serial, chip_idx))
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
        server: CurbHttpServer,
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
        server: CurbHttpServer,
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


class _CurbCircuitEnergyBase(_CurbCircuitBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    # Subclasses set these to pick which server store backs the sensor.
    _unique_id_suffix: str = ""
    _name_suffix: str = ""
    _store_attr: str = ""

    def __init__(
        self,
        server: CurbHttpServer,
        serial: str,
        circuit_idx: int,
    ) -> None:
        super().__init__(server, serial, circuit_idx)
        self._attr_unique_id = (
            f"curb_{serial}_circuit_{circuit_idx + 1}{self._unique_id_suffix}"
        )
        self._attr_name = f"{self._friendly}{self._name_suffix}"

    def _store(self) -> dict[str, dict[int, float]]:
        return getattr(self._server, self._store_attr)

    @property
    def native_value(self) -> float | None:
        wh = self._store().get(self._serial, {}).get(self._idx)
        if wh is None:
            return None
        return wh / 1000.0

    @property
    def available(self) -> bool:
        return self._idx in self._store().get(self._serial, {})


class CurbCircuitEnergySensor(_CurbCircuitEnergyBase):
    # Shared unique_id across both modes so toggling the bidirectional
    # flag doesn't spawn a new entity (and doesn't lose long-term
    # history) — the store's meaning shifts, but the sensor stays.
    _unique_id_suffix = "_energy"
    _name_suffix = " Energy"
    _store_attr = "energy_wh"


class CurbCircuitEnergyProductionSensor(_CurbCircuitEnergyBase):
    _unique_id_suffix = "_energy_production"
    _name_suffix = " Energy Production"
    _store_attr = "energy_wh_production"


class _CurbCircuitLiveBase(_CurbCircuitBase):
    """Base for opt-in live per-circuit measurements (I, PF, VAR).

    Subclasses set the unique_id suffix, the friendly-name suffix, the
    server attribute name backing the sensor, and the usual SensorEntity
    class attrs (device_class / unit / precision).
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    _unique_id_suffix: str = ""
    _name_suffix: str = ""
    _store_attr: str = ""

    def __init__(
        self,
        server: CurbHttpServer,
        serial: str,
        circuit_idx: int,
    ) -> None:
        super().__init__(server, serial, circuit_idx)
        self._attr_unique_id = (
            f"curb_{serial}_circuit_{circuit_idx + 1}{self._unique_id_suffix}"
        )
        self._attr_name = f"{self._friendly}{self._name_suffix}"

    def _store(self) -> dict[str, dict[int, float]]:
        return getattr(self._server, self._store_attr)

    @property
    def native_value(self) -> float | None:
        return self._store().get(self._serial, {}).get(self._idx)

    @property
    def available(self) -> bool:
        return self._idx in self._store().get(self._serial, {})


class CurbCircuitCurrentSensor(_CurbCircuitLiveBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_suggested_display_precision = 2
    _unique_id_suffix = "_current"
    _name_suffix = " Current"
    _store_attr = "latest_current"


class CurbCircuitPowerFactorSensor(_CurbCircuitLiveBase):
    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    # Curb reports signed PF in the −1…1 range, so leave the unit
    # blank (the POWER_FACTOR device class also accepts "%", but that
    # would imply we'd multiplied by 100 — we haven't).
    _attr_native_unit_of_measurement = None
    _attr_suggested_display_precision = 3
    _unique_id_suffix = "_power_factor"
    _name_suffix = " Power Factor"
    _store_attr = "latest_power_factor"


class CurbCircuitReactivePowerSensor(_CurbCircuitLiveBase):
    _attr_device_class = SensorDeviceClass.REACTIVE_POWER
    _attr_native_unit_of_measurement = "var"
    _attr_suggested_display_precision = 1
    _unique_id_suffix = "_reactive_power"
    _name_suffix = " Reactive Power"
    _store_attr = "latest_reactive_power"


class _CurbHubPhaseBase(SensorEntity):
    """Base for opt-in per-phase hub-level sensors (V, Hz).

    These attach to the hub device (not a circuit) because the
    underlying reading is one-per-ADE-chip — typically the two mains
    legs on a split-phase install. `chip_idx` is the index within the
    hub's group list (0 = chip A, 1 = chip B, …).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    _unique_id_suffix: str = ""
    _name_prefix: str = ""
    _store_attr: str = ""

    def __init__(
        self,
        server: CurbHttpServer,
        serial: str,
        chip_idx: int,
    ) -> None:
        self._server = server
        self._serial = serial
        self._chip_idx = chip_idx
        # Match the channel-naming convention (banks of letters) so the
        # phase labels line up with the A/B/C circuit groupings shown
        # in the options form.
        phase_label = chr(ord("A") + chip_idx)
        self._attr_unique_id = (
            f"curb_{serial}_phase_{chip_idx + 1}{self._unique_id_suffix}"
        )
        self._attr_name = f"{self._name_prefix} {phase_label}"
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

    def _store(self) -> dict[str, dict[int, float]]:
        return getattr(self._server, self._store_attr)

    @property
    def native_value(self) -> float | None:
        return self._store().get(self._serial, {}).get(self._chip_idx)

    @property
    def available(self) -> bool:
        return self._chip_idx in self._store().get(self._serial, {})


class CurbHubVoltageSensor(_CurbHubPhaseBase):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_suggested_display_precision = 1
    _unique_id_suffix = "_voltage"
    _name_prefix = "Voltage"
    _store_attr = "latest_voltage"


class CurbHubFrequencySensor(_CurbHubPhaseBase):
    _attr_device_class = SensorDeviceClass.FREQUENCY
    _attr_native_unit_of_measurement = UnitOfFrequency.HERTZ
    _attr_suggested_display_precision = 2
    _unique_id_suffix = "_frequency"
    _name_prefix = "Frequency"
    _store_attr = "latest_frequency"
