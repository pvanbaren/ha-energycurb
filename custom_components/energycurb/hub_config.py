"""Build a v3.1 hub-config.json body for a Curb hub.

Given an 18-circuit list (name / clamp / voltage / inverted per
entry), emit the hub-config.json the Lamarr streamer expects from
/v3/hub_config/<hub_id>.

We only handle the v3.1 hub-config.json here — the legacy pre-v3
config.json and lamarr_config.sh aren't served over HTTP by the hub, so
they're out of scope for this integration.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from .const import (
    CHIP_CHANNELS,
    CLAMP_30A,
    CLAMP_50A,
    CLAMP_100A,
    CONF_CIRCUIT_BIDIRECTIONAL,
    CONF_CIRCUIT_CLAMP,
    CONF_CIRCUIT_INVERTED,
    CONF_CIRCUIT_NAME,
    CONF_CIRCUIT_VOLTAGE,
    DEFAULT_SAMPLE_PERIOD_S,
    NUM_CIRCUITS,
    VOLTAGE_110,
    VOLTAGE_220,
)

CONFIG_VERSION = "3.1"

# Voltage calibration — constant across all Lamarr production units
# (configure_device.py: V_MULTIPLIER / V_OFFSET).
V_MULTIPLIER = 8.6700886e-05
V_OFFSET = 0

# Phase-compensation coefficients (ade.lua PCF_COEF_50HZ / PCF_COEF_60HZ).
PHASE_COEF_50 = 4197540
PHASE_COEF_60 = 4198965

DEFAULT_ENDPOINTS = {
    "hub_config":  "http://homeassistant.local:8989/v3/hub_config",
    "messages":    "http://homeassistant.local:8989/v3/messages",
    "samples":     "http://homeassistant.local:8989/v3/samples",
    "diagnostics": "http://homeassistant.local:8989/v3/diagnostics",
}

# v3.1 CT-clamp multipliers as emitted by Curb's production pipeline,
# keyed by (user-facing clamp label, voltage). Values are copied
# verbatim from reference production hub-config.json files so they
# serialize byte-identically — we can't compute 220V as 2× 110V and
# expect bit-exact output, because Curb's calibration pipeline rounds
# the two voltages independently (visible on XIAMEN30, where the 220V
# entry differs from 2× the 110V entry by one ULP).
_DEFINITION_ID: dict[str, str] = {
    CLAMP_30A:  "XIAMEN30",
    CLAMP_50A:  "XIAMEN50",
    CLAMP_100A: "XIAMEN100",
}

_MULTIPLIERS: dict[str, dict[str, tuple[float, float, float]]] = {
    #               (i_multiplier,            w_multiplier,   var_multiplier)
    CLAMP_30A: {
        VOLTAGE_110: (5.2290755588493e-06,    0.00026897918,  0.00026897918),
        VOLTAGE_220: (1.0458151117699e-05,    0.00053795836,  0.00053795836),
    },
    CLAMP_50A: {
        VOLTAGE_110: (8.73962e-06,            0.00044961108,  0.00044961108),
        VOLTAGE_220: (1.747924e-05,           0.00089922216,  0.00089922216),
    },
    CLAMP_100A: {
        VOLTAGE_110: (1.7609849656268e-05,    0.00091863534,  0.00091863534),
        VOLTAGE_220: (3.5219699312536e-05,    0.00183727068,  0.00183727068),
    },
}


def _build_channel(circuit: dict[str, Any]) -> dict[str, Any] | str:
    clamp = circuit.get(CONF_CIRCUIT_CLAMP)
    voltage = circuit.get(CONF_CIRCUIT_VOLTAGE)
    if clamp not in _MULTIPLIERS or voltage not in _MULTIPLIERS[clamp]:
        # lamarr/config.lua stores empty slots as the bare string 'none'
        return "none"

    i_mul, w_mul, var_mul = _MULTIPLIERS[clamp][voltage]
    sign = -1.0 if circuit.get(CONF_CIRCUIT_INVERTED) else 1.0

    return {
        "clamp_definition_id": _DEFINITION_ID[clamp],
        "i_multiplier":   sign * i_mul,
        "w_multiplier":   sign * w_mul,
        "var_multiplier": sign * var_mul,
        "i_offset": 0,
        "w_offset": 0,
        "var_offset": 0,
        "phase_coef_50": PHASE_COEF_50,
        "phase_coef_60": PHASE_COEF_60,
    }


def _endpoints_for(base_url: str | None) -> dict[str, str]:
    """Endpoint URLs the hub will use after loading this config.

    `base_url` should be `<scheme>://<host>[:port]` — pass the origin the
    hub itself used to GET /v3/hub_config, so every subsequent POST from
    the hub lands back on this same integration listener. Falling back to
    the upstream Curb URLs is only useful for offline testing; on a live
    deploy the Curb cloud is dead and a hub that actually tries those
    will just stop talking to anything.
    """
    if not base_url:
        return dict(DEFAULT_ENDPOINTS)
    base_url = base_url.rstrip("/")
    return {
        "hub_config":  f"{base_url}/v3/hub_config",
        "messages":    f"{base_url}/v3/messages",
        "samples":     f"{base_url}/v3/samples",
        "diagnostics": f"{base_url}/v3/diagnostics",
    }


def build_hub_config(
    serial: str,
    circuits: list[dict[str, Any]],
    *,
    base_url: str | None = None,
    revision: int | None = None,
    sample_period_s: int = DEFAULT_SAMPLE_PERIOD_S,
) -> dict[str, Any]:
    """Return the v3.1 hub-config.json body for one hub.

    `sample_period_s` is the streamer's sample interval in whole
    seconds (minimum 1); it's serialized as `sampling.sample_period_ms =
    sample_period_s * 1000`. Non-integer or sub-1s values get rounded
    and clamped so the config is always well-formed.
    """
    if len(circuits) != NUM_CIRCUITS:
        raise ValueError(
            f"expected {NUM_CIRCUITS} circuits, got {len(circuits)}"
        )

    groups_out = []
    idx = 0
    for n in CHIP_CHANNELS:
        channels_out = [_build_channel(circuits[idx + i]) for i in range(n)]
        idx += n
        groups_out.append({
            "channels": channels_out,
            "v_multiplier": V_MULTIPLIER,
            "v_offset": V_OFFSET,
        })

    # location_id and organization are in the canonical hub-config.json
    # shape. We don't have cloud-assigned values here, so derive a stable
    # UUID from the serial (uuid5 in the DNS namespace is deterministic)
    # and use the "curb" organization the firmware ships with.
    location_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{serial}.energycurb.local"))

    return {
        "version": CONFIG_VERSION,
        "hub_id": serial,
        "location_id": location_id,
        "organization": "curb",
        "revision": int(time.time()) if revision is None else revision,
        "endpoints": _endpoints_for(base_url),
        "sampling": {
            "sample_period_ms": max(1, int(round(sample_period_s))) * 1000,
            "samples_per_post": 1,
        },
        "sensors": {"groups": groups_out},
    }


def _default_circuit_name(idx: int) -> str:
    # 18 circuits labelled A1..A6, B1..B6, C1..C6.
    bank = "ABC"[idx // 6]
    return f"{bank}{(idx % 6) + 1}"


def default_circuits() -> list[dict[str, Any]]:
    """A fresh 18-circuit list with sensible starting values."""
    return [
        {
            CONF_CIRCUIT_NAME: _default_circuit_name(i),
            CONF_CIRCUIT_CLAMP: CLAMP_30A,
            CONF_CIRCUIT_VOLTAGE: VOLTAGE_110,
            CONF_CIRCUIT_INVERTED: False,
            CONF_CIRCUIT_BIDIRECTIONAL: False,
        }
        for i in range(NUM_CIRCUITS)
    ]
