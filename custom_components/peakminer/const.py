# custom_components/peakminer/const.py
"""Constants for the PeakMiner integration."""

DOMAIN = "peakminer"

CONF_HOST = "host"
CONF_PORT = "port"

DEFAULT_PORT = 4068
DEFAULT_NAME = "PeakMiner"

# Seconds between each /summary refresh. Lowered from 30 to 5 so the Online
# bit (and per-GPU hashrate reset) reacts fast when the miner drops.
UPDATE_INTERVAL_SECONDS = 5
