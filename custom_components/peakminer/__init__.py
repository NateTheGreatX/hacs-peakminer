import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

from .const import (
    CONF_API_KEY,
    DOMAIN,
    PEAKMINER_MIN_VERSION_WITH_API_KEY,
)
from .coordinator import PeakMinerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

CONTROL_PLATFORMS = [
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.BUTTON,
]


def _version_tuple(version: str | None) -> tuple[int, ...]:
    """Parse a dotted version string into a comparable integer tuple."""
    parts = [int(p) for p in str(version or "").split(".") if str(p).isdigit()]
    if not parts:
        return (0, 0, 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def _controls_available(api_key: str | None, version: str | None) -> bool:
    """True when the miner speaks the control API (v2.20.0+) *and* a key is set.

    The PeakMiner control endpoints (pause / pool / config / oc and the
    ``--api-control`` levels) shipped with v2.20.0; they also require the API key.
    Older miners only expose the open ``GET /summary`` read API, so control
    entities must not be created for them.
    """
    if not api_key:
        return False
    return _version_tuple(version) >= _version_tuple(
        PEAKMINER_MIN_VERSION_WITH_API_KEY
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up PeakMiner from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    api_key = entry.data.get(CONF_API_KEY)

    coordinator = PeakMinerCoordinator(
        hass,
        entry.data["host"],
        entry.data["port"],
        api_key,
    )

    # Track the key next to the coordinator so service handlers can reach the
    # same client that already has the key wired in.
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Fetch initial data so entities have state on first setup.
    await coordinator.async_config_entry_first_refresh()

    platforms: list[Platform] = list(PLATFORMS)
    miner_version = (coordinator.data or {}).get("version")
    if _controls_available(api_key, miner_version):
        _LOGGER.info(
            "PeakMiner %s: control API available (miner version %s) - enabling controls",
            coordinator.short_host,
            miner_version,
        )
        platforms += CONTROL_PLATFORMS
    else:
        _LOGGER.info(
            "PeakMiner %s: control API unavailable (miner version %s, api_key=%s) - stats only",
            coordinator.short_host,
            miner_version,
            api_key,
        )

    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [*PLATFORMS, *CONTROL_PLATFORMS]
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
