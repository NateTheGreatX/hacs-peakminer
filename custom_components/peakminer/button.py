# custom_components/peakminer/button.py
"""PeakMiner control buttons.

* **Reset OC Settings (per GPU)** — ``DELETE /control/oc/{gpu}``, undoes an API-set
  overclock and puts the card back to the command-line flags / stock. No reboot.
* **Reset OC Settings** — ``DELETE /config/overlay``, removes any API config
  overlay (coin/pools/user/worker + OC) so the miner returns to the flags it was
  started with. The miner restarts itself; the entity waits for /health.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PeakMinerAPIError
from .const import DOMAIN
from .coordinator import PeakMinerCoordinator

_LOGGER = logging.getLogger(__name__)

# Description for the per-GPU OC-restore button (shared instance across GPUs;
# the per-GPU button wraps the description and sets its own name + unique_id).
RESTORE_OC_DESC = ButtonEntityDescription(
    key="reset_oc_settings",
    name="Reset OC Settings",
    icon="mdi:content-discard",
)


class _BaseRestoreOCButton(ButtonEntity):
    """Shared behaviour; subclasses set _gpu_id and the name."""

    def __init__(
        self,
        coordinator: PeakMinerCoordinator,
        gpu_id: int,
        name: str,
        has_entity_name: bool = True,
    ):
        super().__init__()
        self.coordinator = coordinator
        self._gpu_id = gpu_id
        self._attr_name = f"{coordinator.title} {name}"
        self._attr_unique_id = f"peakminer_{coordinator.short_host}_gpu{gpu_id}_reset_oc_settings"
        if has_entity_name:
            self._attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Always interactive. OC changes are in-place and non-fatal."""
        return True

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, f"{self.coordinator.short_host}_gpu_{self._gpu_id}")},
            "name": f"PeakMiner GPU {self._gpu_id}",
            "manufacturer": "PeakMiner",
            "model": f"GPU {self._gpu_id}",
        }

    async def async_press(self) -> None:
        """DELETE /control/oc/{gpu} — back to the command-line / stock."""
        try:
            await self.coordinator.client.restore_gpu_oc(self._gpu_id)
        except PeakMinerAPIError as err:
            _LOGGER.warning("peakminer restore OC GPU %s failed: %s", self._gpu_id, err)
            return
        await self.coordinator.async_refresh()


class _RestoreOCButtonForGPU(_BaseRestoreOCButton):
    """One restore button per GPU."""


class _BackToFlightSheetButton(ButtonEntity):
    """Reset the miner back to its command-line flags via the config overlay.

    ``DELETE /config/overlay`` drops the whole overlay (coin/pools/user/worker +
    OC); the miner restarts itself, so the button waits for /health to come back
    before it's interactive again. Use a 60-second poll with a 2-min timeout.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:flight-landing"

    def __init__(self, coordinator: PeakMinerCoordinator):
        super().__init__()
        self.coordinator = coordinator
        self._attr_name = f"{coordinator.title} Reset OC Settings"
        self._attr_unique_id = f"peakminer_{coordinator.short_host}_back_to_flight_sheet"

    @property
    def available(self) -> bool:
        return True

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self.coordinator.short_host)},
            "name": self.coordinator.title,
            "manufacturer": "PeakMiner",
            "model": "PeakMiner",
            "sw_version": (self.coordinator.data or {}).get("version"),
        }

    async def async_press(self) -> None:
        """DELETE /config/overlay. The miner restarts itself; log the outcome."""
        try:
            resp = await self.coordinator.client.delete_config_overlay()
        except PeakMinerAPIError as err:
            _LOGGER.warning("peakminer back to flight sheet failed: %s", err)
            return
        if isinstance(resp, dict):
            _LOGGER.info("peakminer back to flight sheet: %s", resp)
        # Refresh so config sensors reflect the returned-to-flags state.
        await self.coordinator.async_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the back-to-flight-sheet button + per-GPU OC restore buttons."""
    coordinator: PeakMinerCoordinator = hass.data[DOMAIN][entry.entry_id]
    buttons: list[ButtonEntity] = [_BackToFlightSheetButton(coordinator)]

    # Per-GPU OC restore buttons for the snapshot already known.
    current = [
        g for g in (coordinator.data or {}).get("gpus", [])
        if isinstance(g, dict) and isinstance(g.get("id"), int)
    ]
    for g in current:
        buttons.append(
            _RestoreOCButtonForGPU(
                coordinator, g.get("id"), f"GPU {g.get('id')} Reset OC Settings"
            )
        )

    # Register for future GPUs.
    coordinator.new_gps_callbacks.append(
        lambda gpu_data: async_add_entities(
            [
                _RestoreOCButtonForGPU(
                    coordinator, g.get("id"), f"GPU {g.get('id')} Reset OC Settings"
                )
                for g in gpu_data
                if isinstance(g, dict) and isinstance(g.get("id"), int)
            ]
        )
    )

    async_add_entities(buttons)
