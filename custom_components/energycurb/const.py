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
