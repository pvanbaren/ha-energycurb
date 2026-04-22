"""Config flow for EnergyCurb."""
from __future__ import annotations

import copy
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CLAMP_CHOICES,
    CONF_CIRCUITS,
    CONF_CIRCUIT_CLAMP,
    CONF_CIRCUIT_NAME,
    CONF_CIRCUIT_POLARITY,
    CONF_CIRCUIT_VOLTAGE,
    CONF_DEVICES,
    CONF_HOST,
    CONF_PORT,
    CONF_SERIAL,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
    NUM_CIRCUITS,
    POLARITY_CHOICES,
    VOLTAGE_CHOICES,
)
from .hub_config import _default_circuit_name, default_circuits


class EnergyCurbConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = int(user_input[CONF_PORT])

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"EnergyCurb :{port}",
                data={CONF_HOST: host, CONF_PORT: port},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(
                    CONF_PORT, default=DEFAULT_PORT
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=65535,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return EnergyCurbOptionsFlow(entry)


def _circuit_field(circuit_idx: int) -> str:
    # Flat field names: c1_name, c1_clamp, c1_voltage, c1_polarity, ...
    return f"c{circuit_idx + 1}"


def _clamp_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=CLAMP_CHOICES,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="clamp",
        )
    )


def _voltage_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=VOLTAGE_CHOICES,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="voltage",
        )
    )


def _polarity_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=POLARITY_CHOICES,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="polarity",
        )
    )


class EnergyCurbOptionsFlow(OptionsFlow):
    """Configure the 18 circuits of a specific discovered hub."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._serial: str | None = None

    def _known_serials(self) -> list[str]:
        """Union of discovered serials and any already-configured in options."""
        server = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        live: set[str] = set(server.serials) if server is not None else set()
        stored: set[str] = set(self._entry.options.get(CONF_DEVICES, {}).keys())
        return sorted(live | stored)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        serials = self._known_serials()
        if not serials:
            return self.async_abort(reason="no_devices")

        if len(serials) == 1:
            self._serial = serials[0]
            return await self.async_step_circuits()

        if user_input is not None:
            self._serial = user_input[CONF_SERIAL]
            return await self.async_step_circuits()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERIAL): SelectSelector(
                        SelectSelectorConfig(
                            options=serials,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    def _current_circuits(self) -> list[dict[str, Any]]:
        assert self._serial is not None
        devices = self._entry.options.get(CONF_DEVICES, {})
        existing = devices.get(self._serial, {}).get(CONF_CIRCUITS)
        if existing and len(existing) == NUM_CIRCUITS:
            return existing
        return default_circuits()

    def _circuits_schema(self, defaults: list[dict[str, Any]]) -> vol.Schema:
        schema: dict[Any, Any] = {}
        for i, ch in enumerate(defaults):
            base = _circuit_field(i)
            schema[vol.Required(
                f"{base}_name", default=ch.get(CONF_CIRCUIT_NAME, _default_circuit_name(i))
            )] = TextSelector()
            schema[vol.Required(
                f"{base}_clamp", default=ch.get(CONF_CIRCUIT_CLAMP, CLAMP_CHOICES[0])
            )] = _clamp_selector()
            schema[vol.Required(
                f"{base}_voltage", default=ch.get(CONF_CIRCUIT_VOLTAGE, VOLTAGE_CHOICES[0])
            )] = _voltage_selector()
            schema[vol.Required(
                f"{base}_polarity", default=ch.get(CONF_CIRCUIT_POLARITY, POLARITY_CHOICES[0])
            )] = _polarity_selector()
        return vol.Schema(schema)

    async def async_step_circuits(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._serial is not None

        if user_input is not None:
            circuits: list[dict[str, Any]] = []
            for i in range(NUM_CIRCUITS):
                base = _circuit_field(i)
                circuits.append({
                    CONF_CIRCUIT_NAME: user_input[f"{base}_name"],
                    CONF_CIRCUIT_CLAMP: user_input[f"{base}_clamp"],
                    CONF_CIRCUIT_VOLTAGE: user_input[f"{base}_voltage"],
                    CONF_CIRCUIT_POLARITY: user_input[f"{base}_polarity"],
                })

            new_options = copy.deepcopy(dict(self._entry.options))
            devices = new_options.setdefault(CONF_DEVICES, {})
            devices[self._serial] = {CONF_CIRCUITS: circuits}
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="circuits",
            data_schema=self._circuits_schema(self._current_circuits()),
            description_placeholders={"serial": self._serial},
        )
