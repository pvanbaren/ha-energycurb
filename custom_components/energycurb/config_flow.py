"""Config flow for Curb."""
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
from homeassistant.data_entry_flow import section
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
    CONF_CIRCUIT_BIDIRECTIONAL,
    CONF_CIRCUIT_CLAMP,
    CONF_CIRCUIT_INVERTED,
    CONF_CIRCUIT_NAME,
    CONF_CIRCUIT_VOLTAGE,
    CONF_DEVICES,
    CONF_HOST,
    CONF_PORT,
    CONF_SAMPLE_PERIOD_S,
    CONF_SERIAL,
    DEFAULT_HOST,
    DEFAULT_NUM_CIRCUITS,
    DEFAULT_PORT,
    DEFAULT_SAMPLE_PERIOD_S,
    DOMAIN,
    VOLTAGE_CHOICES,
)
from .http_server import CurbHttpServer, enqueue_hub_message
from .hub_config import _default_circuit_name, default_circuits


class CurbConfigFlow(ConfigFlow, domain=DOMAIN):
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
                title=f"Curb :{port}",
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
        return CurbOptionsFlow(entry)


def _section_key(circuit_idx: int) -> str:
    # One section per circuit: c1, c2, …, c18. strings.json names each
    # section with its physical position label (A1–C6).
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


class CurbOptionsFlow(OptionsFlow):
    """Configure the per-circuit settings for a specific discovered hub."""

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

    def _num_circuits(self) -> int:
        """Channel count for the current hub, derived from its detected
        chip layout via the running server (or the default fallback)."""
        assert self._serial is not None
        server: CurbHttpServer | None = self.hass.data.get(DOMAIN, {}).get(
            self._entry.entry_id
        )
        if server is None:
            return DEFAULT_NUM_CIRCUITS
        return server.num_circuits_for(self._serial)

    def _current_circuits(self) -> list[dict[str, Any]]:
        assert self._serial is not None
        n = self._num_circuits()
        devices = self._entry.options.get(CONF_DEVICES, {})
        existing = devices.get(self._serial, {}).get(CONF_CIRCUITS)
        if existing and len(existing) >= n:
            return existing[:n]
        defaults = default_circuits(n)
        if existing:
            return list(existing) + defaults[len(existing):]
        return defaults

    def _current_sample_period(self) -> int:
        assert self._serial is not None
        devices = self._entry.options.get(CONF_DEVICES, {})
        val = devices.get(self._serial, {}).get(CONF_SAMPLE_PERIOD_S)
        if val is None:
            return DEFAULT_SAMPLE_PERIOD_S
        try:
            return max(1, int(round(float(val))))
        except (TypeError, ValueError):
            return DEFAULT_SAMPLE_PERIOD_S

    def _circuits_schema(
        self,
        sample_period_s: int,
        defaults: list[dict[str, Any]],
    ) -> vol.Schema:
        fields: dict[Any, Any] = {
            vol.Required(
                CONF_SAMPLE_PERIOD_S, default=sample_period_s
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=60,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }
        for i, ch in enumerate(defaults):
            sub = vol.Schema(
                {
                    vol.Required(
                        "name",
                        default=ch.get(CONF_CIRCUIT_NAME, _default_circuit_name(i)),
                    ): TextSelector(),
                    vol.Required(
                        "clamp",
                        default=ch.get(CONF_CIRCUIT_CLAMP, CLAMP_CHOICES[0]),
                    ): _clamp_selector(),
                    vol.Required(
                        "voltage",
                        default=ch.get(CONF_CIRCUIT_VOLTAGE, VOLTAGE_CHOICES[0]),
                    ): _voltage_selector(),
                    vol.Required(
                        "inverted",
                        default=bool(ch.get(CONF_CIRCUIT_INVERTED, False)),
                    ): bool,
                    vol.Required(
                        "bidirectional",
                        default=bool(ch.get(CONF_CIRCUIT_BIDIRECTIONAL, False)),
                    ): bool,
                }
            )
            fields[vol.Required(_section_key(i))] = section(sub, {"collapsed": False})
        return vol.Schema(fields)

    async def async_step_circuits(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._serial is not None

        if user_input is not None:
            circuits: list[dict[str, Any]] = []
            for i in range(self._num_circuits()):
                sub = user_input[_section_key(i)]
                circuits.append({
                    CONF_CIRCUIT_NAME: sub["name"],
                    CONF_CIRCUIT_CLAMP: sub["clamp"],
                    CONF_CIRCUIT_VOLTAGE: sub["voltage"],
                    CONF_CIRCUIT_INVERTED: bool(sub["inverted"]),
                    CONF_CIRCUIT_BIDIRECTIONAL: bool(sub["bidirectional"]),
                })

            new_options = copy.deepcopy(dict(self._entry.options))
            devices = new_options.setdefault(CONF_DEVICES, {})
            devices[self._serial] = {
                CONF_CIRCUITS: circuits,
                CONF_SAMPLE_PERIOD_S: max(
                    1, int(round(float(user_input[CONF_SAMPLE_PERIOD_S])))
                ),
            }

            # Tell the hub to fetch the new hub-config.json on its next
            # 5-second message poll, instead of waiting up to 5 minutes
            # for the periodic config refresh. hass.data persists across
            # the reload that async_create_entry triggers, so the
            # enqueued notification is still there when the new server
            # comes up.
            enqueue_hub_message(
                self.hass, self._entry, self._serial, {"type": "config"}
            )

            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="circuits",
            data_schema=self._circuits_schema(
                self._current_sample_period(),
                self._current_circuits(),
            ),
            description_placeholders={"serial": self._serial},
        )
