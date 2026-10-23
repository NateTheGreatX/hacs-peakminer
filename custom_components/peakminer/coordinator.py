from __future__ import annotations

import logging
import re
import inspect
from collections.abc import Callable
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PeakMinerClient
from .const import UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class PeakMinerCoordinator(DataUpdateCoordinator):
    """Poll ``/summary`` for one PeakMiner instance and own its API client.

    The coordinator subclasses ``DataUpdateCoordinator`` so the per-GPU sensor
    entities auto-update when the poll fires (``CoordinatorEntity`` subscribes to
    the coordinator's listeners). The returned data is the raw ``/summary`` JSON
    with ``gpus`` extracted into ``data["gpus"]`` for sensor.py.

    Control operations (pause / pool / oc / config) go over the shared
    ``self.client`` -- an authenticated ``PeakMinerClient`` that honours the
    2.20.0 ``--api-key`` lock. Reads there return JSON; writes raise
    ``PeakMinerAPIError`` so a switch/number/button entity can surface the
    miner's own ``{"error": …, "field": …}`` message.
    """

    def __init__(self, hass, host, port, api_key: str | None = None):
        super().__init__(
            hass,
            _LOGGER,
            name=f"PeakMiner {re.sub(r"[^0-9A-Za-z]", "", host or "") or "0"}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.host = host
        self.port = int(port or 4068)
        self.api_key = api_key
        self.short_host = re.sub(r"[^0-9A-Za-z]", "", host or "") or "0"
        self.title = f"PeakMiner {self.short_host}"

        # Shared authenticated HTTP client for /summary + all control routes.
        # Built here (not in the session helper) so the same session+key is
        # reused for reads and writes across the whole entry's lifetime.
        self.client = PeakMinerClient(hass, host, self.port, api_key)
        self.session = self.client.session  # alias, kept for back-compat/tests

        # Whether the last /summary poll succeeded. False is not fatal: we keep
        # returning the last-good data so entities never flip to "cannot
        # connect". sensor.py uses this to flip the Online binary sensor and
        # reset hashrate to zero.
        self.online = False
        self._last_good: dict = {}

        # Portainer-style "known set + callback" so per-GPU entities can be
        # added on a later refresh (not just once at setup, when the gpus list
        # may not have populated yet).
        self.known_gpus: set = set()
        self.new_gps_callbacks: list = []

        # Per-platform "rig-level entity once" tracking. Each control platform
        # (switch/select/number/button) adds its rig-wide entity (e.g. the
        # rig pause toggle, pool select) exactly once, then registers a
        # per-GPU callback for the rest.
        self._rig_added: set = set()

    async def async_config_entry_first_refresh(self) -> None:
        """Fetch the initial /summary so entities have state on first setup."""
        await self.async_refresh()

    async def _async_update_data(self) -> dict:
        # Fetch a 15s window of data; on error we log it but *don't raise*.
        # Raising would flip every entity to "cannot connect" in HA. Instead we
        # remember whether the last poll succeeded and return the last-good data
        # so entities keep showing state (the Online sensor tells the truth).
        try:
            data = await self.client.summary()
            if not isinstance(data, dict) or not data.get("version"):
                _LOGGER.warning(
                    "PeakMiner %s:  empty/invalid /summary — last online=%s",
                    self.short_host,
                    self.online,
                )
                self.online = False
                self._handle_failure()
                return {**self._last_good, "gpus": []}

            # The /summary payload includes ``gpus`` at the top level. Pop it out
            # so it becomes ``data["gpus"]`` for sensor.py, and keep the rest of
            # the fields the miner reported.
            gpus = data.get("gpus", [])
            if not isinstance(gpus, list):
                gpus = []
            data["gpus"] = list(gpus)

            self.online = bool(data.get("version"))
            self._last_good = dict(data)
            self.data = data

            # Detect GPUs we haven't seen yet and notify the sensor platform so
            # it can add per-GPU entities/devices on this refresh instead of
            # requiring a restart. Only track real int ids in the list.
            current = {
                g.get("id")
                for g in gpus
                if isinstance(g, dict) and isinstance(g.get("id"), int)
            }
            new_gpus = current - self.known_gpus
            if new_gpus:
                _LOGGER.debug("PeakMiner new GPUs detected: %s", sorted(new_gpus))
                self.known_gpus.update(new_gpus)
                new_gpu_data = [
                    g for g in gpus if isinstance(g, dict) and g.get("id") in new_gpus
                ]
                for cb in list(self.new_gps_callbacks):
                    # `async_add_entities` is a coroutine in modern HA, so a
                    # plain `cb(...)` would create a coroutine that gets silently
                    # discarded and no GPUs would ever be added. Run whichever
                    # form it returns (async or sync).
                    result = cb(new_gpu_data)
                    if inspect.iscoroutine(result):
                        await result

            _LOGGER.debug(
                "PeakMiner summary from %s online=%s gpus=%s",
                self.short_host,
                self.online,
                len(gpus),
            )
        except UpdateFailed as err:
            _LOGGER.warning("PeakMiner fetch failed: %s", err)
            self._handle_failure()
        except Exception as err:  # noqa: BLE001 - we never surface HA errors
            _LOGGER.warning("PeakMiner unexpected error: %s", err)
            self._handle_failure()
        return dict(self.data or {})

    def _handle_failure(self) -> None:
        """Drop online, keep the last-good snapshot for entities."""
        self.online = False
        self.data = dict(self._last_good or {})
        self.data["gpus"] = list(self.data.get("gpus", []))
