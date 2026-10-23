# custom_components/peakminer/select.py
"""PeakMiner control selects (pool + coin).

* **Pool Selector** — options from ``/summary.pool.pools``, current from
  ``/summary.pool.index``. A selection is applied with
  ``POST /control/pool {"index": …}`` (same as the `l`/`h` hotkeys).
* **Coin** — read-only mirror of ``/summary.algo`` so the UI shows what the rig
  mines; writing is done through the *Change Config* / *Change Coin* services
  on the *main* device, which restart the miner with the new coin.

Pool switching returns ``409`` when only one pool is configured (nothing to
switch to); that is surfaced as a logger warning, not a service failure.
"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PeakMinerAPIError
from .const import DOMAIN
from .coordinator import PeakMinerCoordinator

_LOGGER = logging.getLogger(__name__)

# Fallback coin options, used only until /config.coin has been read.
_FALLBACK_COIN_OPTIONS = [
    "pearl",
    "csd",
    "midstate",
    "alphanumeric",
    "quantus",
]


def _pool_pools(coordinator: PeakMinerCoordinator) -> list[str]:
    pool = (coordinator.data or {}).get("pool") or {}
    pools = pool.get("pools") if isinstance(pool, dict) else None
    return [str(p) for p in (pools or []) if p]


def _pool_index(coordinator: PeakMinerCoordinator) -> int | None:
    pool = (coordinator.data or {}).get("pool") or {}
    idx = pool.get("index") if isinstance(pool, dict) else None
    return idx if isinstance(idx, int) else None


def _pool_url(current_index: int | None, pools: list[str]) -> str | None:
    if current_index is None or current_index < 0:
        return None
    if 0 <= current_index < len(pools):
        return pools[current_index]
    return None


class PeakMinerPoolSelect(SelectEntity):
    """Select the active pool from the failover list.

    Options are the pool URLs in order; selecting one fires
    ``POST /control/pool {"index": …}``. The current option is the live pool
    index. Only rendered when more than one pool is configured — with a single
    pool the miner answers 409 and the entity is hidden.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:pool"
    _attr_device_class = "switch"

    def __init__(self, coordinator: PeakMinerCoordinator):
        super().__init__()
        self.coordinator = coordinator
        self._attr_name = f"{coordinator.title} Pool"
        self._attr_unique_id = f"peakminer_{coordinator.short_host}_pool"

    @property
    def available(self) -> bool:
        return self.coordinator.online

    @property
    def current_option(self) -> str | None:
        if not self.coordinator.online:
            return None
        return _pool_url(_pool_index(self.coordinator), _pool_pools(self.coordinator))

    @property
    def options(self) -> list[str]:
        return _pool_pools(self.coordinator)

    @property
    def native_options(self) -> list[str]:
        return self.options

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self.coordinator.short_host)},
            "name": self.coordinator.title,
            "manufacturer": "PeakMiner",
            "model": "PeakMiner",
            "sw_version": (self.coordinator.data or {}).get("version"),
        }

    async def async_select_option(self, option: str) -> None:
        """Select a pool by URL -> index, then POST /control/pool."""
        pools = _pool_pools(self.coordinator)
        if option not in pools:
            _LOGGER.warning("peakminer pool select: unknown option %r", option)
            return
        try:
            await self.coordinator.client.switch_pool(pools.index(option))
        except PeakMinerAPIError as err:
            _LOGGER.warning("peakminer %s pool switch failed: %s", option, err)
            raise err
        # /control/pool does not restart the miner, but the index does change;
        # refresh so the sensor reflects the new pool.
        await self.coordinator.async_refresh()


class PeakMinerCoinSelect(SelectEntity):
    """Read-only mirror of the rig's coin (algo) shown as a select for context.

    This is not the coin picker — changing the coin restarts the miner and is
    done through the *Change Coin* / *Change Config* services (config level is
    what applies coin changes). A plain sensor would work too, but a select
    lets automations compare without an extra entity.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:coin"
    _attr_device_class = "switch"

    def __init__(self, coordinator: PeakMinerCoordinator):
        super().__init__()
        self.coordinator = coordinator
        # A coin can be any of the miner's supported algorithms; the options are
        # the full list we expose (read from /config.coin when available).
        self._attr_name = f"{coordinator.title} Coin"
        self._attr_unique_id = f"peakminer_{coordinator.short_host}_coin"
        # The select platform reads capability attributes (including
        # ``options``) while the entity is being added, before
        # ``async_added_to_hass`` runs, so seed the options up front.
        self._attr_options: list[str] = list(_FALLBACK_COIN_OPTIONS)
        self._coin_options: list[str] = list(_FALLBACK_COIN_OPTIONS)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        cfg = await self.coordinator.client.config()
        if isinstance(cfg, dict) and (cfg.get("coin") or cfg.get("algo")):
            self._coin_options = [str(cfg.get("coin") or cfg.get("algo"))]
            self._attr_options = list(self._coin_options)
        # Fall back to a fixed known list if /config is not readable yet.
        if not self._coin_options:
            self._attr_options = list(_FALLBACK_COIN_OPTIONS)

    @property
    def options(self) -> list[str]:
        """Return the selectable coin options."""
        return self._attr_options

    @property
    def available(self) -> bool:
        return self.coordinator.online

    @property
    def current_option(self) -> str | None:
        if not self.coordinator.online:
            return None
        data = self.coordinator.data or {}
        return data.get("algo") or (data.get("coin"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the pool select + coin select on the main (rig) device."""
    coordinator: PeakMinerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []
    # Pool: only meaningful with >1 configured pool (single-pool switch is a
    # 409 from the miner; hide it).
    if len(_pool_pools(coordinator)) > 1:
        entities.append(PeakMinerPoolSelect(coordinator))
    entities.append(PeakMinerCoinSelect(coordinator))
    async_add_entities(entities)
