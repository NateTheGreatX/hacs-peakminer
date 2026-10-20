from __future__ import annotations

import logging
import re
import inspect
from collections.abc import Callable
from datetime import timedelta

import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class PeakMinerCoordinator(DataUpdateCoordinator):
    """Fetch data from a single PeakMiner instance /summary endpoint.

    The returned object is the raw JSON from {host}:{port}/summary minus the
    ``gpus`` list. The ``gpus`` array is extracted into ``data["gpus"]`` so
    that sensor.py can iterate over it for per-GPU entities.
    """

    def __init__(self, hass, host, port):
        self.host = host
        self.port = port
        self.short_host = re.sub(r"[^0-9A-Za-z]", "", host or "") or "0"
        self.title = f"PeakMiner {self.short_host}"
        self.url = f"http://{host}:{port}/summary"
        self._session = async_get_clientsession(hass)

        super().__init__(
            hass,
            _LOGGER,
            name=f"PeakMiner {self.short_host}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )

        # Whether the last /summary poll succeeded. False is not fatal: we keep
        # returning the last-good data so entities never flip to "cannot
        # connect". sensor.py uses this to flip the Online binary sensor and
        # reset hashrate to zero.
        self.online = False
        self._last_good: dict = {}

        # Portainer-style "known set + callback" so per-GPU entities can be
        # added on a later refresh (not just once at setup, when the gpus list
        # may not have populated yet).
        self.known_gpus: set[int] = set()
        self.new_gps_callbacks: list[Callable[..., None]] = []

    async def _async_update_data(self) -> dict:
        # Fetch a 15s window of data; on error we log it but *don't raise*.
        # Raising would flip every entity to "cannot connect" in HA. Instead we
        # remember whether the last poll succeeded and return the last-good data
        # so entities keep showing state (the Online sensor tells the truth).
        try:
            async with async_timeout.timeout(15):
                resp = await self._session.get(self.url)
                if resp.status != 200:
                    _LOGGER.warning(
                        "PeakMiner returned status %s from %s", resp.status, self.url
                    )
                    self.online = False
                    self._handle_failure()
                    return dict(self._last_good or {})

                data = await resp.json()

                # The /summary payload includes ``gpus`` at the top level.
                # Pop it out so it becomes ``data["gpus"]`` for sensor.py, and
                # keep whatever top-level fields the miner reported.
                gpus = data.get("gpus", [])
                if not isinstance(gpus, list):
                    gpus = []
                data["gpus"] = list(gpus)

                # GPUs only count as "good" if we actually have some — a 200
                # but empty list is still stale.
                self.online = bool(data.get("version"))
                self._last_good = data

                # Detect GPUs we haven't seen yet and notify the sensor platform
                # so it can add per-GPU entities/devices on this refresh instead
                # of requiring a restart. Only track real int ids in the list.
                current = {
                    g.get("id")
                    for g in gpus
                    if isinstance(g, dict) and isinstance(g.get("id"), int)
                }
                new_gpus = current - self.known_gpus
                if new_gpus:
                    _LOGGER.debug("PeakMiner new GPUs detected: %s", sorted(new_gpus))
                    self.known_gpus.update(new_gpus)
                    new_gpu_data = [g for g in gpus if isinstance(g, dict) and g.get("id") in new_gpus]
                    for cb in list(self.new_gps_callbacks):
                        # `async_add_entities` is a coroutine in modern HA, so a
                        # plain `cb(new_gpu_data)` would create a coroutine that
                        # gets silently discarded and no GPUs would ever be added.
                        # Run whichever form it returns (async or sync).
                        result = cb(new_gpu_data)
                        if inspect.iscoroutine(result):
                            await result

                _LOGGER.debug("PeakMiner summary from %s", self.url)
                return data
        except UpdateFailed as err:
            # Non-200 already handled above; anything here is a real fetch
            # error (timeout, DNS, connection reset). Keep the good data coming.
            _LOGGER.warning("PeakMiner fetch failed: %s", err)
            self.online = False
            r = dict(self._last_good or {})
            r["gpus"] = list(r.get("gpus", []))
            return r
        except Exception as err:  # noqa: BLE001 - we intentionally never surface HA errors
            _LOGGER.warning("PeakMiner unexpected error: %s", err)
            self.online = False
            r = dict(self._last_good or {})
            r["gpus"] = list(r.get("gpus", []))
            return r
