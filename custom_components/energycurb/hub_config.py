"""Build a v3.1 hub-config.json body for a Curb hub.

Mirrors the subset of ../energycurb/configure_device.py that we need at
runtime: given an 18-circuit list (name / clamp / voltage / polarity per
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
    CONF_CIRCUIT_CLAMP,
    CONF_CIRCUIT_NAME,
    CONF_CIRCUIT_POLARITY,
    CONF_CIRCUIT_VOLTAGE,
    NUM_CIRCUITS,
    POLARITY_NEGATIVE,
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
    "hub_config":  "http://border.prod.energycurb.com/v3/hub_config",
    "messages":    "http://border.prod.energycurb.com/v3/messages",
    "samples":     "http://border.prod.energycurb.com/v3/samples",
    "diagnostics": "http://border.prod.energycurb.com/v3/diagnostics",
}

# Canonical v3.1 CT-clamp multipliers, keyed by the user-facing clamp
# label. `definition_id` is the string the firmware stores in
# `clamp_definition_id`.
CT_V3: dict[str, dict[str, Any]] = {
    CLAMP_30A: {
        "definition_id": "XIAMEN30",
        "i_multiplier": 5.2290755588493e-06,
        "w_multiplier": 0.00026897918,
        "var_multiplier": 0.00026897918,
    },
    CLAMP_50A: {
        "definition_id": "XIAMEN50",
        "i_multiplier": 8.73962e-06,
        "w_multiplier": 0.00044961108,
        "var_multiplier": 0.00044961108,
    },
    CLAMP_100A: {
        "definition_id": "XIAMEN100",
        "i_multiplier": 1.7609849656268e-05,
        "w_multiplier": 0.00091863534,
        "var_multiplier": 0.00091863534,
    },
}


def _build_channel(circuit: dict[str, Any]) -> dict[str, Any] | str:
    clamp = circuit.get(CONF_CIRCUIT_CLAMP)
    if clamp not in CT_V3:
        # lamarr/config.lua stores empty slots as the bare string 'none'
        return "none"

    base = CT_V3[clamp]
    # 240V circuits in a split-phase panel see line-to-line voltage but
    # the group's v_multiplier is calibrated line-to-neutral, so the
    # hub under-reports power by 2x. Compensate on the channel's
    # energy multipliers; current is unaffected.
    w_scale = 2.0 if circuit.get(CONF_CIRCUIT_VOLTAGE) == VOLTAGE_220 else 1.0
    sign = -1.0 if circuit.get(CONF_CIRCUIT_POLARITY) == POLARITY_NEGATIVE else 1.0

    return {
        "clamp_definition_id": base["definition_id"],
        "i_multiplier":   sign * base["i_multiplier"],
        "w_multiplier":   sign * base["w_multiplier"] * w_scale,
        "var_multiplier": sign * base["var_multiplier"] * w_scale,
        "i_offset": 0,
        "w_offset": 0,
        "var_offset": 0,
        "phase_coef_50": PHASE_COEF_50,
        "phase_coef_60": PHASE_COEF_60,
    }


def build_hub_config(
    serial: str,
    circuits: list[dict[str, Any]],
    *,
    revision: int | None = None,
) -> dict[str, Any]:
    """Return the v3.1 hub-config.json body for one hub."""
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
        "endpoints": dict(DEFAULT_ENDPOINTS),
        "sampling": {"sample_period_ms": 1000, "samples_per_post": 1},
        "load_control": {
            "is_enabled": False,
            "disable_until": 0,
            "utc_offset_hours": 0,
            "enforcement_windows": [],
            "enforcement_window_exceptions": [],
            "load_circuits": [],
            "relays": [],
            "monitor_interval_mins": 30,
            "monitor_interval_guard_mins": 2,
            "monitor_average_mins": 2,
            "control_minimum_off_mins": 5,
            "control_maximum_off_mins": 5,
            "control_minimum_delay_mins": 5,
            "control_priority_step_mins": 1,
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
            CONF_CIRCUIT_VOLTAGE: "110V",
            CONF_CIRCUIT_POLARITY: "+",
        }
        for i in range(NUM_CIRCUITS)
    ]
