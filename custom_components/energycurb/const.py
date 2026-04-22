"""Constants for the EnergyCurb integration."""
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
CONF_CIRCUIT_NAME = "name"
CONF_CIRCUIT_CLAMP = "clamp"
CONF_CIRCUIT_VOLTAGE = "voltage"
CONF_CIRCUIT_POLARITY = "polarity"

CLAMP_30A = "30A"
CLAMP_50A = "50A"
CLAMP_100A = "100A"
CLAMP_CHOICES = [CLAMP_100A, CLAMP_50A, CLAMP_30A]

VOLTAGE_110 = "110V"
VOLTAGE_220 = "220V"
VOLTAGE_CHOICES = [VOLTAGE_110, VOLTAGE_220]

POLARITY_POSITIVE = "+"
POLARITY_NEGATIVE = "-"
POLARITY_CHOICES = [POLARITY_POSITIVE, POLARITY_NEGATIVE]

# Chip/group layout of a full 00614 hub: 4 ADE chips sum to 18 channels.
# The hub-config.json schema lays channels out in this exact order.
CHIP_CHANNELS = [6, 6, 3, 3]
