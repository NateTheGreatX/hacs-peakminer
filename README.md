# PeakMiner Home Assistant Integration

A [Home Assistant](https://www.home-assistant.io) integration for
[PeakMiner](https://github.com/nrees-agreserves/peakminer) — a distributed
`xmrig`/AMD-miner controller. It polls the miner's HTTP `/summary` endpoint and
exposes the rig's stats as Home Assistant sensors.

![version: 0.2.0](https://img.shields.io/badge/version-0.2.0-brightgreen)
![iot_class: local_polling](https://img.shields.io/badge/iot_class-local--polling-blue)

## Features

- **One entry per rig** — point it at any PeakMiner HTTP API endpoint.
- **Combined (main) sensors** — 17 top-level stats on a single **Rig** device
  (combined hashrate, power, efficiency, shares, ETA, pool info, …).
- **Per-GPU sensors** — a **separate device per GPU** (e.g. *RTX 4060 0*,
  *RTX 4060 1*), 11 sensors each (utilization, hashrate, temperature, fan, power,
  core/mem clocks, shares, status).
- **Dynamic GPUs** — GPUs that appear or change at runtime show up on the next
  poll, no restart or manual entity add required.
- **TH/s by default** — hashrate fields arrive in H/s from the miner and are
  scaled to **TH/s** / **TH/W** for readability.
- **Pool connection boolean** — `Pool Connected` doubles as a `on/off` sensor for
  automations.
- **GPU power % control** — per-GPU **wattage limit as a percentage of the card's
  own default** (e.g. `80%`), applied in place through the miner's OC control API.
  Manage power in percent — no max/min wattage arithmetic, no restart.

## Requirements

- Home Assistant Core **≥ 2025.3.0**
- A running PeakMiner instance serving `GET /summary` over HTTP.

### Controls / API

Control entities (pause switch, pool select, GPU power %, OC reset, flight-sheet
button) require PeakMiner **≥ 2.20.0** with an API key configured via
`--api-key` / `PEAK_API_KEY` and control levels enabled via `--api-control`
(`pause,pool,config,oc` or `all`). See the
[PeakMiner HTTP API](https://github.com/peakminer/peakminer/blob/main/API.md) for
the full control-endpoint reference (`/control/pause`, `/control/resume`,
`/control/pool`, `/control/oc`, `/control/state`, `/config`, `/config/overlay`).

Older miners get stats only — no control entities are created for them when the
version is below the control-API minimum.

## Installation

### HACS

1. Open **HACS → Integrations → … → Custom →** and add the repository
   `nrees-agreserves/hacs-peakminer` (or `NateTheGreatX/hacs-peakminer`) as a
   **custom repository**.
2. Install **PeakMiner**.
3. Restart Home Assistant if prompted, then add the integration via
   **Settings → Devices & Services → Add integration → PeakMiner**.

### Manual

1. Copy the `custom_components/peakminer` folder into your HA `custom_components/`
   directory.
2. Restart Home Assistant.
3. Add the integration via **Settings → Devices & Services → Add integration**.

## Configuration

The config flow is fully UI-driven. You supply:

| Field        | Description                          | Default |
|--------------|--------------------------------------|---------|
| **Host**     | Hostname/IP of the PeakMiner API     | —       |
| **Port**     | Port of the `/summary` endpoint      | `4068`  |
| **API Key**  | API key for the miner's control API  | unset*  |

\* Empty / unset means the miner is running its older **open (unauthenticated)**
API, so stats work without a key and control endpoints are unavailable.

No authentication is required when no key is set — PeakMiner's API is an
unauthenticated HTTP GET by default.

### Changing / adding the API key later: **Reconfigure**

If you added the integration before you had an API key (or you want to point it
at a different miner / port), use **Reconfigure** on the existing entry:

**Settings → Devices & Services → PeakMiner → ⋮ (three dots) → Reconfigure**

The reconfigure form shows the current host / port / API key and lets you change
any of them. After submitting it validates the new settings against the miner and
then reloads the entry — the new API key (or host / port) is picked up without you
having to delete and re-add the integration. Clearing the API key field switches
back to the open (unauthenticated) API.

## Entities

### Main / combined (one per rig device)

| Sensor             | Unit   | Notes                 |
|--------------------|--------|-----------------------|
| Version            | —      | PeakMiner version     |
| Algorithm          | —      | e.g. `pearl`          |
| Uptime             | s      |                         |
| Dev Fee            | %      |                         |
| Hashrate           | TH/s   | combined, /1e12       |
| Power              | W      |                         |
| Hashrate/Watt      | TH/W   | /1e12                 |
| Efficiency         | %      |                         |
| Accepted Shares    | shares | total                 |
| Invalid Shares     | shares | total                 |
| Last Share At      | —      | unix epoch            |
| ETA Next Share     | s      |                         |
| Effort             | %      |                         |
| Pool URL           | —      |                         |
| Pool Ping          | ms     |                         |
| Pool Difficulty    | —      |                         |
| Pool Connected     | on/off | boolean, for automations |

### Per-GPU (one device per GPU, 11 sensors each)

| Sensor            | Unit     | Notes            |
|-------------------|----------|------------------|
| Utilization       | %        |                  |
| Hashrate          | TH/s     | /1e12            |
| Hashrate/Watt     | TH/W     | /1e12            |
| Temperature       | °C       |                  |
| Fan Speed         | %        |                  |
| Power             | W        |                  |
| Mem Clock         | MHz      |                  |
| Core Clock        | MHz      |                  |
| Accepted Shares   | shares   | total            |
| Invalid Shares    | shares   | total            |
| Status            | string   | GPU status field |

## Development / Troubleshooting

- Data is polled every **5 seconds** (`UPDATE_INTERVAL_SECONDS` in `const.py`).
- Logs can be found by searching for `peakminer` in the HA logs.
- If per-GPU devices don't appear, **Unload & Delete** the entry and
  **Add** it again — that rebuilds the full device/entity set cleanly.

## License

MIT
