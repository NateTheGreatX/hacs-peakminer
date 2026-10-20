# custom_components/peakminer/sensor.py
"""Sensor platform for the PeakMiner integration.

Two sensor groups are created for every PeakMiner instance:

* **Main (combined)** sensors that aggregate the top-level ``/summary`` stats
  (combined hashrate, power, pool info, etc.). These live on a single
  "PeakMiner" device and carry no ``gpu_id``.
* **Per-GPU** sensors, one per entry in the ``gpus`` array. Each GPU becomes
  its own device, exposing per-GPU hashrate, utilization, temperature, fan,
  clocks, etc.

Hashrate-style fields arrive in H/s from the miner and are divided by 1e12 so
they display in TH/s / TH/W.
"""
from __future__ import annotations

import inspect
from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PeakMinerCoordinator

# 1 TH/s = 1e12 H/s. Hashrate fields arrive in H/s and are scaled to TH/s.
TH_DIVISOR = 1e12

# Attribute id -> sensor definition for the combined (main) stats.
# 6-tuple: (attr_id, name, state_class, unit, icon, data_key, divisor)
_COMBINED_FIELDS = (
    ("version", "Version", None, None, "mdi:tag", None, 1.0),
    ("algo", "Algorithm", None, None, "mdi:chip", None, 1.0),
    ("uptime", "Uptime", SensorStateClass.MEASUREMENT, "s", "mdi:timer-sand", None, 1.0),
    ("dev_fee_percent", "Dev Fee", SensorStateClass.MEASUREMENT, "%", "mdi:hand-coin", None, 1.0),
    ("hashrate", "Hashrate", SensorStateClass.MEASUREMENT, "TH/s", "mdi:gauge", None, TH_DIVISOR),
    ("power_w", "Power", SensorStateClass.MEASUREMENT, "W", "mdi:flash", None, 1.0),
    ("hashrate_per_watt", "Hashrate/Watt", SensorStateClass.MEASUREMENT, "TH/W", "mdi:flash", None, TH_DIVISOR),
    ("efficiency_pct", "Efficiency", SensorStateClass.MEASUREMENT, "%", "mdi:percent", None, 1.0),
    ("accepted_shares", "Accepted Shares", SensorStateClass.TOTAL_INCREASING, "shares", "mdi:check-circle", None, 1.0),
    ("invalid_shares", "Invalid Shares", SensorStateClass.TOTAL_INCREASING, "shares", "mdi:dismiss-circle", None, 1.0),
    ("last_share_at", "Last Share At", None, None, "mdi:history", "last_share_at", 1.0),
    ("eta_share_secs", "ETA Next Share", SensorStateClass.MEASUREMENT, "s", "mdi:timer-outline", None, 1.0),
    ("effort_pct", "Effort", SensorStateClass.MEASUREMENT, "%", "mdi:hammer-wrench", None, 1.0),
    ("pool_url", "Pool URL", None, None, "mdi:server-network", "pool.url", 1.0),
    ("pool_ping_ms", "Pool Ping", SensorStateClass.MEASUREMENT, "ms", "mdi:network-latency", "pool.ping_ms", 1.0),
    ("pool_difficulty", "Pool Difficulty", SensorStateClass.MEASUREMENT, None, "mdi:chart-candlestick", "pool.difficulty", 1.0),
    ("pool_connected", "Pool Connected", None, None, "mdi:connection", "pool.connected", 1.0),
)

# Attribute id -> sensor definition for the per-GPU stats.
# 6-tuple: (attr_id, name, state_class, unit, icon, divisor)
GPU_FIELDS = (
    ("utilization_pct", "Utilization", SensorStateClass.MEASUREMENT, "%", "mdi:chart-bar", 1.0),
    ("hashrate", "Hashrate", SensorStateClass.MEASUREMENT, "TH/s", "mdi:gauge", TH_DIVISOR),
    ("hashrate_per_watt", "Hashrate/Watt", SensorStateClass.MEASUREMENT, "TH/W", "mdi:flash", TH_DIVISOR),
    ("temperature_c", "Temperature", SensorStateClass.MEASUREMENT, "\u00b0C", "mdi:thermometer", 1.0),
    ("fan_pct", "Fan Speed", SensorStateClass.MEASUREMENT, "%", "mdi:fan", 1.0),
    ("power_w", "Power", SensorStateClass.MEASUREMENT, "W", "mdi:flash", 1.0),
    ("mem_clock_mhz", "Mem Clock", SensorStateClass.MEASUREMENT, "MHz", "mdi:clock", 1.0),
    ("core_clock_mhz", "Core Clock", SensorStateClass.MEASUREMENT, "MHz", "mdi:clock", 1.0),
    ("accepted_shares", "Accepted Shares", SensorStateClass.TOTAL_INCREASING, "shares", "mdi:check-circle", 1.0),
    ("invalid_shares", "Invalid Shares", SensorStateClass.TOTAL_INCREASING, "shares", "mdi:dismiss-circle", 1.0),
    ("status", "Status", None, None, "mdi:gauge", 1.0),
)


class _BasePeakMinerSensor(CoordinatorEntity, SensorEntity):
    """Base class for PeakMiner sensors (combined or per-GPU)."""

    def __init__(
        self,
        coordinator: PeakMinerCoordinator,
        gpu_id: int | None,
        attr_id: str,
        name_suffix: str,
        unit: str | None = None,
        state_class: SensorStateClass | None = None,
        icon: str | None = None,
        data_key: str | None = None,
        divisor: float = 1.0,
    ):
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._gpu_id = gpu_id  # None -> combined stats
        self._attr_data_key = data_key or attr_id
        self._attr_divisor = divisor
        # Per-GPU sensors fold the gpu index into the id so GPU 0 and GPU 1 do
        # not collide (a collision makes HA silently drop the duplicates — the
        # reason per-GPU entities were missing). Main sensors have no index.
        if gpu_id is not None:
            self._attr_unique_id = f"peakminer_{coordinator.short_host}_gpu{gpu_id}_{attr_id}"
        else:
            self._attr_unique_id = f"peakminer_{coordinator.short_host}_{attr_id}"
        self._device_name = coordinator.title

        suffix = f" GPU {gpu_id}" if gpu_id is not None else ""
        self._attr_name = f"{coordinator.title}{suffix} {name_suffix}".strip()
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def device_info(self) -> dict:
        """Group entities into devices.

        Main (combined) sensors share one device per miner instance. Per-GPU
        sensors each get their own device with a distinct identifier so every
        GPU shows up individually in Home Assistant.
        """
        data = self.coordinator.data or {}
        if self._gpu_id is None:
            return {
                "identifiers": {(DOMAIN, self.coordinator.short_host)},
                "name": self._device_name,
                "manufacturer": "PeakMiner",
                "model": "PeakMiner",
                "sw_version": data.get("version"),
            }

        gpu = self._get_raw() or {}
        gname = gpu.get("name") or f"GPU {self._gpu_id}"
        # NB: no `via` link to the main device. Recent HA removed `via` from the
        # device registry's async_get_or_create(), so declaring it here crashes
        # the entity add. Each GPU still becomes its own device, just not nested.
        return {
            "identifiers": {(DOMAIN, f"{self.coordinator.short_host}_gpu_{self._gpu_id}")},
            "name": f"{gname} {self._gpu_id}",
            "manufacturer": "PeakMiner",
            "model": gname,
        }

    def _lookup(self, obj: dict, key: str) -> object:
        """Walk dot-paths into nested dicts, e.g. 'pool.difficulty'."""
        for part in key.split("."):
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                return None
        return obj

    def _get_raw(self):
        if self._gpu_id is None:
            return None
        for gpu in (self.coordinator.data or {}).get("gpus", []):
            if isinstance(gpu, dict) and gpu.get("id") == self._gpu_id:
                return gpu
        return None

    def _state_object(self):
        """Resolve the /summary value for this sensor, scaled to display units."""
        data = self.coordinator.data
        if data is None:
            return None
        if self._gpu_id is not None:
            raw = self._get_raw()
            if raw is None:
                return None
            val = self._lookup(raw, self._attr_data_key)
        else:
            val = self._lookup(data, self._attr_data_key)
        return self._scale(val)

    def _scale(self, val):
        """Divide hash-style values to TH and round to 3 decimals for display."""
        if (
            isinstance(val, (int, float))
            and not isinstance(val, bool)
            and self._attr_divisor != 1.0
        ):
            return round(val / self._attr_divisor, 3)
        return val


class _PeakMinerSensor(_BasePeakMinerSensor):
    """Numeric / percentage sensor."""

    @property
    def state(self) -> StateType:
        val = self._state_object()
        if val is None:
            return None
        if isinstance(val, bool):
            return "True" if val else "False"
        if isinstance(val, (int, float)):
            return val
        return str(val)


class _PeakMinerStatusSensor(_BasePeakMinerSensor):
    """String / boolean status sensor."""

    @property
    def state(self) -> StateType:
        val = self._state_object()
        if val is None:
            return None
        if isinstance(val, bool):
            return "True" if val else "False"
        return str(val)

    @property
    def is_on(self) -> bool:
        """Expose pool connected as a boolean on/off sensor for automations."""
        val = self._state_object()
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() == "true"
        return False


def _make(
    coordinator: PeakMinerCoordinator,
    gpu_id: int | None,
    attr_id: str,
    name: str,
    state_class: SensorStateClass | None,
    unit: str | None,
    icon: str | None,
    data_key: str | None,
    divisor: float = 1.0,
) -> _BasePeakMinerSensor:
    """Build a single sensor instance.

    ``data_key`` is the exact /summary path to read (dotted paths like
    ``pool.ping_ms`` are supported). Pass ``None`` to fall back to ``attr_id``.
    ``divisor`` scales numeric values (TH = divide by 1e12).
    """
    cls = _PeakMinerStatusSensor if state_class is None else _PeakMinerSensor
    return cls(
        coordinator=coordinator,
        gpu_id=gpu_id,
        attr_id=attr_id,
        name_suffix=name,
        unit=unit,
        state_class=state_class,
        icon=icon,
        data_key=data_key,
        divisor=divisor,
    )


def _build_main_sensors(coordinator: PeakMinerCoordinator) -> list[_BasePeakMinerSensor]:
    """Create the top-level combined (main) sensors.

    Fields are 7-tuples: (attr_id, name, state_class, unit, icon, data_key, divisor).
    """
    sensors: list[_BasePeakMinerSensor] = []
    for spec in _COMBINED_FIELDS:
        attr_id, name, state_class, unit, icon, data_key, divisor = spec
        sensors.append(
            _make(
                coordinator=coordinator,
                gpu_id=None,
                attr_id=attr_id,
                name=name,
                state_class=state_class,
                unit=unit,
                icon=icon,
                data_key=data_key,
                divisor=divisor,
            )
        )
    return sensors


def _build_gps_for(
    coordinator: PeakMinerCoordinator, gpus: list
) -> list[_BasePeakMinerSensor]:
    """Build one 11-sensor set per GPU dict in ``gpus``.

    Used both for the initial snapshot and by the coordinator callback that
    adds brand-new GPUs on a later refresh.
    """
    if not isinstance(gpus, list):
        return []

    sensors: list[_BasePeakMinerSensor] = []
    for gpu in gpus:
        if not isinstance(gpu, dict):
            continue
        index = gpu.get("id")
        if not isinstance(index, int):
            continue
        for attr_id, name, state_class, unit, icon, divisor in GPU_FIELDS:
            sensors.append(
                _make(
                    coordinator=coordinator,
                    gpu_id=index,
                    # The gpu index is folded into the unique id by the sensor
                    # base class; the lookup key is the bare field name.
                    attr_id=attr_id,
                    name=name,
                    state_class=state_class,
                    unit=unit,
                    icon=icon,
                    data_key=attr_id,
                    divisor=divisor,
                )
            )
    return sensors


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PeakMiner sensors from a config entry.

    Main (combined) sensors are added once. Per-GPU sensors are added via a
    coordinator callback so GPUs that appear after setup (or after an empty
    first refresh) still get their own device — no restart needed.
    """
    coordinator: PeakMinerCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.title = entry.title

    # The main sensors' device_info creates the main "Rig" device; each GPU
    # sensor's device_info creates its own separate device. No manual device
    # registry call is needed (and `via` nesting is avoided: this HA version
    # no longer accepts it).

    # Callback invoked by the coordinator whenever a brand-new GPU id shows up.
    # It returns the `async_add_entities` result (a coroutine on modern HA) so
    # the coordinator can actually run it.
    def _async_add_new_gps(gpu_list: list):
        sensors = _build_gps_for(coordinator, gpu_list)
        if sensors:
            return async_add_entities(sensors)

    coordinator.new_gps_callbacks.append(_async_add_new_gps)

    # Initial add: main sensors + every GPU already known to the coordinator.
    main_sensors = _build_main_sensors(coordinator)
    current_gpus = [
        g
        for g in (coordinator.data or {}).get("gpus", [])
        if isinstance(g, dict) and isinstance(g.get("id"), int)
    ]
    # `async_add_entities` is a coroutine in modern HA; run whichever form it is
    # so the initial per-GPU set is actually added (not just created & dropped).
    result = async_add_entities(main_sensors + _build_gps_for(coordinator, current_gpus))
    if inspect.iscoroutine(result):
        await result
