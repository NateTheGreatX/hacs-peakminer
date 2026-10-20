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

        # Portainer-style "known set + callback" so per-GPU entities can be
        # added on a later refresh (not just once at setup, when the gpus list
        # may not have populated yet).
        self.known_gpus: set[int] = set()
        self.new_gps_callbacks: list[Callable[..., None]] = []

    async def _async_update_data(self) -> dict:
        try:
            async with async_timeout.timeout(15):
                resp = await self._session.get(self.url)
                if resp.status != 200:
                    raise UpdateFailed(
                        f"PeakMiner returned status {resp.status}"
                    )

                data = await resp.json()

                # The /summary payload includes ``gpus`` at the top level.
                # Pop it out so it becomes ``data["gpus"]`` for sensor.py, and
                # keep whatever top-level fields the miner reported.
                gpus = data.get("gpus", [])
                if not isinstance(gpus, list):
                    gpus = []
                data["gpus"] = list(gpus)

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
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(
                f"Error fetching PeakMiner summary from {self.url}: {err}"
            ) from err
