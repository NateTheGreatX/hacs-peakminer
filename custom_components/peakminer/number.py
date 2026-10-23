# custom_components/peakminer/number.py
"""PeakMiner GPU power (wattage) percentage number entities.

* **GPU Power %** — set a card's power limit as a percentage of the card's own
  default limit (``"<NN>%"`` in the miner API). The write goes through the OC
  control level (``PUT /control/oc/{gpu}`` with the whole profile, or ``PATCH``
  when the API already set that profile); the driver answers with ``applied`` /
  ``skipped`` lists, and refusals (out-of-range values, etc.) are logged.

  Users manage power in percent so they never have to reason about per-card
  max/min wattages by hand.
"""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PeakMinerAPIError
from .const import DOMAIN
from .coordinator import PeakMinerCoordinator

_LOGGER = logging.getLogger(__name__)


def _gpu_entry(coordinator: PeakMinerCoordinator, gpu_id: int) -> dict | None:
    """The /summary GPU dict for ``gpu_id`` (or None when not seen yet)."""
    for g in (coordinator.data or {}).get("gpus") or []:
        if isinstance(g, dict) and g.get("id") == gpu_id:
            return g
    return None


def _power_percent_from_entry(entry: object) -> float | None:
    """Percent (0–100) of the card's own default limit, or None when unknown.

    The OC profile carries ``power`` either as a ``"<NN>%"`` string (API-set
    profiles) or as a bare number (CLI-set profiles, effectively watts). Only the
    API-set form maps cleanly onto a percentage the user can manage.
    """
    if not isinstance(entry, dict):
        return None
    source = entry.get("source")
    profile = entry.get("profile")
    raw = profile.get("power") if isinstance(profile, dict) else None
    if raw is None:
        return None
    value: float | None = None
    if isinstance(raw, str):
        text = raw.strip()
        if text.endswith("%"):
            try:
                value = float(text[:-1])
            except ValueError:
                value = None
    elif isinstance(raw, (int, float)) and source == "api":
        value = float(raw)
    return value


class PeakMinerGpuPowerNumber(NumberEntity):
    """GPU power limit as a percentage of the card's own default limit."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:gauge"

    def __init__(self, coordinator: PeakMinerCoordinator, gpu_id: int):
        super().__init__()
        self.coordinator = coordinator
        self._gpu_id = gpu_id
        self._attr_name = f"{coordinator.title} GPU {gpu_id} Power %"
        self._attr_unique_id = f"peakminer_{coordinator.short_host}_gpu{gpu_id}_power"
        self._power_pct: float | None = None

    @property
    def available(self) -> bool:
        """Always interactive; miner-side refusals surface via ``skipped``."""
        return True

    @property
    def native_value(self) -> float | None:
        """Current power limit in percent of the card's own default."""
        return self._power_pct

    @property
    def native_min_value(self) -> float:
        return 0.0

    @property
    def native_max_value(self) -> float:
        return 100.0

    @property
    def native_step(self) -> float:
        return 1.0

    @property
    def device_info(self) -> dict:
        gpu = _gpu_entry(self.coordinator, self._gpu_id) or {}
        gname = gpu.get("name") or f"GPU {self._gpu_id}"
        return {
            "identifiers": {(DOMAIN, f"{self.coordinator.short_host}_gpu_{self._gpu_id}")},
            "name": f"{gname} {self._gpu_id}",
            "manufacturer": "PeakMiner",
            "model": gname,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        try:
            entries = await self.coordinator.client.get_oc() or []
        except PeakMinerAPIError:
            # Control level off / miner unreachable: leave the value unknown.
            return
        entry = next(
            (e for e in entries if isinstance(e, dict) and e.get("id") == self._gpu_id),
            None,
        )
        self._power_pct = _power_percent_from_entry(entry)

    async def async_set_native_value(self, value: float) -> None:
        """Set the GPU power limit in percent of the card's own default."""
        percent = float(value)
        try:
            resp = await self.coordinator.client.set_gpu_power(self._gpu_id, percent)
        except PeakMinerAPIError as err:
            _LOGGER.warning(
                "peakminer GPU %s power set to %.0f%% failed: %s", self._gpu_id, percent, err
            )
            return
        if isinstance(resp, dict) and resp.get("skipped"):
            _LOGGER.warning(
                "peakminer GPU %s power set to %.0f%% skipped: %s",
                self._gpu_id,
                percent,
                resp.get("skipped"),
            )
            return
        self._power_pct = percent
        await self.coordinator.async_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the per-GPU GPU power % number entities."""
    coordinator: PeakMinerCoordinator = hass.data[DOMAIN][entry.entry_id]

    numbers: list[NumberEntity] = [
        PeakMinerGpuPowerNumber(coordinator, g["id"])
        for g in (coordinator.data or {}).get("gpus") or []
        if isinstance(g, dict) and isinstance(g.get("id"), int)
    ]
    coordinator.new_gps_callbacks.append(
        lambda gpu_data: async_add_entities(
            [
                PeakMinerGpuPowerNumber(coordinator, g.get("id"))
                for g in gpu_data
                if isinstance(g, dict) and isinstance(g.get("id"), int)
            ]
        )
    )
    async_add_entities(numbers)