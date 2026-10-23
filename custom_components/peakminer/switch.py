# custom_components/peakminer/switch.py
"""PeakMiner pause/resume switches (per rig and per GPU).

Backs onto the 2.20.0 control endpoints:

* POST /control/pause   {"gpus":[id,...]}   (or {})  -> all cards
* POST /control/resume  {"gpus":[id,...]}   (or {})  -> all cards

``turn_off`` pauses, ``turn_on`` resumes. State (``is_on``) reflects mining
status — on when the target card(s) are mining, off when paused — read from
/summary (``status`` is ``"paused"`` while held, ``null`` while mining), so the
switch also follows thermal/schedule pauses, not just the API pause.

Per the API contract, resume only clears the ``api`` reason, so a card held by
thermal stays paused after an API resume; its switch correctly shows the card
is still held.
"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PeakMinerAPIError
from .const import DOMAIN
from .coordinator import PeakMinerCoordinator

_LOGGER = logging.getLogger(__name__)


def _rig_device_info(coordinator: PeakMinerCoordinator) -> dict:
    return {
        "identifiers": {(DOMAIN, coordinator.short_host)},
        "name": coordinator.title,
        "manufacturer": "PeakMiner",
        "model": "PeakMiner",
        "sw_version": (coordinator.data or {}).get("version"),
    }


class PeakMinerPauseSwitch(SwitchEntity):
    """Pause (off) / Resume (on) switch for the whole rig or one card."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:pause-play"
    _attr_device_class = "switch"

    def __init__(
        self,
        coordinator: PeakMinerCoordinator,
        name: str,
        gpu_id: int | None = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self._name = name
        self._gpu_id = gpu_id  # None -> rig-wide, int -> single card index
        self._attr_name = f"{coordinator.title} {name}"
        self._attr_unique_id = (
            f"peakminer_{coordinator.short_host}_rig_pause"
            if gpu_id is None
            else f"peakminer_{coordinator.short_host}_gpu{gpu_id}_pause"
        )

    def is_paused(self) -> bool:
        """True while the target card(s) are held (paused) in /summary."""
        if self._gpu_id is None:
            # Rig-wide: paused if any real GPU exists and is paused.
            gpus = [g for g in (self.coordinator.data or {}).get("gpus", []) if isinstance(g, dict)]
            if not gpus:
                return not self.coordinator.online
            return any(g.get("status") == "paused" for g in gpus)
        for g in (self.coordinator.data or {}).get("gpus", []):
            if isinstance(g, dict) and g.get("id") == self._gpu_id:
                return g.get("status") == "paused"
        return False

    @property
    def is_on(self) -> bool:
        """On while mining (not paused); off while held."""
        return not self.is_paused()

    @property
    def available(self) -> bool:
        """The switch stays interactive even while the rig is paused, so a held
        card can always be resumed from Home Assistant."""
        return True

    @property
    def device_info(self) -> dict:
        if self._gpu_id is None:
            return _rig_device_info(self.coordinator)
        gpu = {}
        for g in (self.coordinator.data or {}).get("gpus", []):
            if isinstance(g, dict) and g.get("id") == self._gpu_id:
                gpu = g
                break
        gname = gpu.get("name") or f"GPU {self._gpu_id}"
        return {
            "identifiers": {(DOMAIN, f"{self.coordinator.short_host}_gpu_{self._gpu_id}")},
            "name": f"{gname} {self._gpu_id}",
            "manufacturer": "PeakMiner",
            "model": gname,
        }

    async def async_turn_on(self) -> None:
        """Resume mining: all cards for the rig switch, that card otherwise."""
        if self._gpu_id is None:
            gpu_ids = None
        else:
            gpu_ids = [self._gpu_id]
        try:
            await self.coordinator.client.resume_gpus(gpu_ids)
        except PeakMinerAPIError as err:
            _LOGGER.warning("peakminer %s resume failed: %s", self._name, err)
            return
        await self.coordinator.async_refresh()

    async def async_turn_off(self) -> None:
        """Pause mining: all cards for the rig switch, that card otherwise."""
        if self._gpu_id is None:
            gpu_ids = None
        else:
            gpu_ids = [self._gpu_id]
        try:
            await self.coordinator.client.pause_gpus(gpu_ids)
        except PeakMinerAPIError as err:
            _LOGGER.warning("peakminer %s pause failed: %s", self._name, err)
            return
        await self.coordinator.async_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the rig pause switch + all GPUs known now; register future ones."""
    coordinator: PeakMinerCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Per-GPU switches are added whenever the coordinator sees a new GPU.
    coordinator.new_gps_callbacks.append(
        lambda gpu_data: async_add_entities(
            [
                PeakMinerPauseSwitch(coordinator, f"GPU {g.get('id')} Pause", gpu_id=g.get("id"))
                for g in gpu_data
                if isinstance(g, dict) and isinstance(g.get("id"), int)
            ]
        )
    )

    switches: list[PeakMinerPauseSwitch] = [
        PeakMinerPauseSwitch(coordinator, "Mining Pause", gpu_id=None)
    ]
    # Existing GPUs are added inline (the new_gpu callback fires only for GPUs
    # appearing after setup).
    switches += [
        PeakMinerPauseSwitch(coordinator, f"GPU {g.get('id')} Pause", gpu_id=g.get("id"))
        for g in (coordinator.data or {}).get("gpus", [])
        if isinstance(g, dict) and isinstance(g.get("id"), int)
    ]
    async_add_entities(switches)
