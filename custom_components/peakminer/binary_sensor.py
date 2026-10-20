# custom_components/peakminer/binary_sensor.py
"""Binary sensor platform for the PeakMiner integration.

Exposes a single ``Online`` binary sensor on the main "Rig" device that
reflects whether the most recent /summary poll succeeded. When the miner is
unreachable the coordinator keeps returning the last-good data (so other
sensors never flip to "cannot connect") and zeroes out hashrate, but the
Online sensor flips to off.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PeakMinerCoordinator

ONLINE_DESCRIPTION = BinarySensorEntityDescription(
    key="online",
    name="Online",
    icon="mdi:check-network",
)


def _build_online_sensor(
    coordinator: PeakMinerCoordinator,
) -> "OnlineBinarySensor":
    """Build the single Online binary sensor bound to the main device."""
    return OnlineBinarySensor(coordinator)


class OnlineBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Online/offline state for a PeakMiner instance."""

    entity_description = ONLINE_DESCRIPTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: PeakMinerCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_unique_id = f"peakminer_{coordinator.short_host}_online"

    @property
    def available(self) -> bool:
        """Mark unavailable only if we have no data at all."""
        return self.coordinator.data is not None

    @property
    def device_info(self) -> dict:
        """Attach the online sensor to the main "Rig" device."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.short_host)},
            "name": self.coordinator.title,
            "manufacturer": "PeakMiner",
            "model": "PeakMiner",
            "sw_version": (self.coordinator.data or {}).get("version"),
        }

    @property
    def is_on(self) -> bool:
        """True while the latest /summary poll succeeds."""
        return self.coordinator.online


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the PeakMiner online binary sensor."""
    coordinator: PeakMinerCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.title = entry.title
    async_add_entities(
        [
            _build_online_sensor(coordinator),
        ]
    )
