"""Constants for the Curb integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "energycurb"
PLATFORMS = [Platform.SENSOR, Platform.SWITCH]

CONF_HOST = "host"
CONF_PORT = "port"

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8989

# Fallback layout used before the hub's first samples POST tells us how
# many channels it actually has. Standard 4-chip 00614-class hubs land
# at [6,6,3,3] = 18; Lite 2-chip hubs (00615/00619/00625) land at [6,6]
# = 12. The samples handler overrides these per-serial as soon as a
# real payload arrives.
DEFAULT_CHIP_CHANNELS = [6, 6, 3, 3]
DEFAULT_NUM_CIRCUITS = sum(DEFAULT_CHIP_CHANNELS)
WH_PER_SEC_TO_W = 3600

SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"
SIGNAL_UPDATE_FMT = f"{DOMAIN}_update_{{serial}}"

# Per-device options shape: entry.options[CONF_DEVICES][serial][CONF_CIRCUITS]
# is a list of per-circuit dicts with CONF_CIRCUIT_* keys, sized to match
# the hub's detected channel count.
CONF_DEVICES = "devices"
CONF_CIRCUITS = "circuits"
CONF_SERIAL = "serial"
CONF_SAMPLE_PERIOD_S = "sample_period_s"
CONF_SHOW_CURRENT = "show_current"
CONF_SHOW_POWER_FACTOR = "show_power_factor"
CONF_SHOW_REACTIVE_POWER = "show_reactive_power"
CONF_CIRCUIT_NAME = "name"
CONF_CIRCUIT_CLAMP = "clamp"
CONF_CIRCUIT_VOLTAGE = "voltage"
CONF_CIRCUIT_INVERTED = "inverted"
CONF_CIRCUIT_BIDIRECTIONAL = "bidirectional"

DEFAULT_SAMPLE_PERIOD_S = 60
DEFAULT_SHOW_CURRENT = False
DEFAULT_SHOW_POWER_FACTOR = False
DEFAULT_SHOW_REACTIVE_POWER = False

# A standard 4-chip hub wires voltage transformers only to chips A and
# B; chips C and D have a floating voltage pin that reads as a few
# hundred millivolts of noise. We use this threshold to decide whether
# a group's `v` field represents a real measurement worth surfacing as
# a sensor — anything below counts as "no voltage reference here", so
# its V / Hz sensors are suppressed and its `w` is approximated from a
# neighbor phase's voltage. 10 V is well above the floating-pin noise
# floor and well below any real mains brown-out.
VOLTAGE_PRESENT_THRESHOLD_V = 10.0

# Option-value strings must match HA's translation-key regex
# `[a-z0-9-_]+` (no leading/trailing - or _) so they can key into the
# selector translations in strings.json. Display labels ("100 A", etc.)
# come from those translations and are unaffected.
CLAMP_30A = "30a"
CLAMP_50A = "50a"
CLAMP_100A = "100a"
CLAMP_XIAMEN100THIN = "xiamen100thin"
CLAMP_ROGOWSKI80100 = "rogowski80100"
CLAMP_CHOICES = [
    CLAMP_100A,
    CLAMP_XIAMEN100THIN,
    CLAMP_50A,
    CLAMP_30A,
    CLAMP_ROGOWSKI80100,
]

VOLTAGE_110 = "110v"
VOLTAGE_220 = "220v"
VOLTAGE_CHOICES = [VOLTAGE_110, VOLTAGE_220]
