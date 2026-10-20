import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

from .const import DOMAIN
from .coordinator import PeakMinerCoordinator

_LOGGER = logging.getLogger(__name__)

# The main "combined stats" sensor + all per-GPU sensors are sensor entities,
# which is what the config flow / manifest reports.
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up PeakMiner from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = PeakMinerCoordinator(
        hass,
        entry.data["host"],
        entry.data["port"],
    )

    # Fetch initial data so entities have state on first setup.
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    ):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
