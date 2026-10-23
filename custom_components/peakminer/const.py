# custom_components/peakminer/const.py
"""Constants for the PeakMiner integration."""

DOMAIN = "peakminer"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"
CONF_NAME = "name"

DEFAULT_PORT = 4068
DEFAULT_NAME = "PeakMiner"

# Seconds between each /summary refresh. Lowered from 30 to 5 so the Online
# bit (and per-GPU hashrate reset) reacts fast when the miner drops.
UPDATE_INTERVAL_SECONDS = 5

# Per-request HTTP timeouts. /summary is polled in tight loops so it keeps a
# short ceiling; control endpoints (config, OC) are user-triggered and the miner
# may briefly serialise/write, so they get a little longer headroom.
DEFAULT_REQUEST_TIMEOUT = 15
DEFAULT_CONTROL_TIMEOUT = 20

# Version this feature set targets (controls/config API added in v2.20.0).
PEAKMINER_MIN_VERSION_WITH_API_KEY = "2.20.0"

# Service / entity control level names mirrored from --api-control.
CONTROL_LEVEL_PAUSE = "pause"
CONTROL_LEVEL_POOL = "pool"
CONTROL_LEVEL_CONFIG = "config"
CONTROL_LEVEL_OC = "oc"
