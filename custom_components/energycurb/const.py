"""Constants for the Curb integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "energycurb"
PLATFORMS = [Platform.SENSOR]

CONF_HOST = "host"
CONF_PORT = "port"

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8989

NUM_CIRCUITS = 18
WH_PER_SEC_TO_W = 3600

SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"
SIGNAL_UPDATE_FMT = f"{DOMAIN}_update_{{serial}}"

# Per-device options shape: entry.options[CONF_DEVICES][serial][CONF_CIRCUITS]
# is a list of NUM_CIRCUITS dicts with CONF_CIRCUIT_* keys.
CONF_DEVICES = "devices"
CONF_CIRCUITS = "circuits"
CONF_SERIAL = "serial"
CONF_SAMPLE_PERIOD_S = "sample_period_s"
CONF_CIRCUIT_NAME = "name"
CONF_CIRCUIT_CLAMP = "clamp"
CONF_CIRCUIT_VOLTAGE = "voltage"
CONF_CIRCUIT_INVERTED = "inverted"
CONF_CIRCUIT_BIDIRECTIONAL = "bidirectional"

DEFAULT_SAMPLE_PERIOD_S = 1

# Option-value strings must match HA's translation-key regex
# `[a-z0-9-_]+` (no leading/trailing - or _) so they can key into the
# selector translations in strings.json. Display labels ("100 A", etc.)
# come from those translations and are unaffected.
CLAMP_30A = "30a"
CLAMP_50A = "50a"
CLAMP_100A = "100a"
CLAMP_ROGOWSKI80100 = "rogowski80100"
CLAMP_CHOICES = [CLAMP_100A, CLAMP_50A, CLAMP_30A, CLAMP_ROGOWSKI80100]

VOLTAGE_110 = "110v"
VOLTAGE_220 = "220v"
VOLTAGE_CHOICES = [VOLTAGE_110, VOLTAGE_220]

# Chip/group layout of a full 00614 hub: 4 ADE chips sum to 18 channels.
# The hub-config.json schema lays channels out in this exact order.
CHIP_CHANNELS = [6, 6, 3, 3]
