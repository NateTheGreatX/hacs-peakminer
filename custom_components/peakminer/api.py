# custom_components/peakminer/api.py
"""Async HTTP client for the PeakMiner v2.20.0 HTTP API.

Read (GET /summary, /health, /config, /control/state, /control/oc) is old;
the ``--api-key`` lock and the ``--api-control <levels>`` write endpoints
(pause, pool, config, oc) are new in v2.20.0. Any control level needs the key
and an enabled ``-a`` bind; a level that is off answers 404 (not 403).

Reads return the parsed JSON (or ``None`` on any failure) so the coordinator and
entities never surface an HTTP error as an HA error. Writes raise
``PeakMinerAPIError`` carrying the miner's ``{"error": …, "field": …}`` body so
a service or entity can show what actually went wrong.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import async_timeout

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.json import json_loads

from .const import (
    DEFAULT_CONTROL_TIMEOUT,
    DEFAULT_PORT,
    DEFAULT_REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

# Human label per HTTP status so services can show e.g. "level-off" instead of a
# raw code. 429 (throttled/debounced) and 409 (not-available) are not "errors"
# per se but we still name them.
HTTP_STATUS_TO_NAME = {
    400: "bad-request",
    401: "unauthenticated",      # key missing / wrong
    404: "level-off",            # control level for this route is off
    405: "method-off",           # PUT /config while the config level is off
    409: "not-available",        # single pool / single device / not applicable
    422: "not-applied",          # unknown JSON key, or driver took nothing
    429: "throttled",            # debounced / capped (Retry-After)
}


class PeakMinerAPIError(Exception):
    """Raised when a PeakMiner control/read call is rejected by the miner."""

    def __init__(
        self,
        message: str,
        status: int | None = None,
        field: str | None = None,
        retry_after: int | None = None,
        method: str | None = None,
        path: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.field = field
        self.retry_after = retry_after
        self.method = method
        self.path = path


class _Resp:
    """Parsed result of one request, shared by read and write paths."""

    def __init__(self, status: int | None, body: Any, error: str | None = None):
        self.status = status
        self.body = body
        self.error = error

    @property
    def ok(self) -> bool:
        """200/202 success. None (timeout/connect) is not ok but not a raise)."""
        return self.status in (200, 202)


class PeakMinerClient:
    """HTTP client bound to one ``{host}:{port}`` endpoint (no trailing path)."""

    def __init__(
        self,
        hass,
        host: str,
        port: int | None = None,
        api_key: str | None = None,
    ):
        self.host = host
        self.port = int(port or DEFAULT_PORT)
        self.base_url = f"http://{host}:{self.port}"
        self.session = async_get_clientsession(hass)
        self.api_key = api_key
        self.short_host = (host or "").split(":")[0] or "0"

    # ------------------------------------------------------------------ headers
    def _headers(self) -> dict[str, str]:
        """Request headers, carrying the API key when one is set.

        ``None`` key -> no header, which forwards the older (open) API. A key is
        sent as ``X-API-Key``; the miner also accepts ``Authorization: Bearer``
        but the X- form is what we always emit.
        """
        if not self.api_key:
            return {}
        return {"X-API-Key": str(self.api_key)}

    # ------------------------------------------------------------------ low level
    async def _send(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> _Resp:
        """Perform one request and return a parsed ``_Resp``.

        Never raises for an HTTP failure. On a timeout / connection error the
        status is ``None``; on a bad or empty body the parsed value is ``None``.
        """
        path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{path}"
        headers = self._headers()
        timeout = (
            DEFAULT_REQUEST_TIMEOUT if "control" not in path else DEFAULT_CONTROL_TIMEOUT
        )
        try:
            async with async_timeout.timeout(timeout):
                if method == "get":
                    resp = await self.session.get(url, headers=headers)
                elif method == "post":
                    resp = await self.session.post(url, json=body or {}, headers=headers)
                elif method == "put":
                    resp = await self.session.put(url, json=body or {}, headers=headers)
                elif method == "patch":
                    resp = await self.session.patch(url, json=body or {}, headers=headers)
                else:  # delete
                    resp = await self.session.delete(url, headers=headers)

                text = await resp.text()
                parsed: Any = None
                error: str | None = None
                if text:
                    try:
                        parsed = await resp.json(content_type=None)
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("PeakMiner %s %s non-JSON body: %s", method, url, text)
                        parsed = _try_json(text)
                    if isinstance(parsed, dict) and parsed.get("error"):
                        error = parsed.get("error")
                return _Resp(resp.status, parsed, error)
        except TimeoutError:
            _LOGGER.debug("PeakMiner client timeout %s %s", method, url)
        except Exception as err:  # noqa: BLE001 - never raise into a coordinator
            _LOGGER.warning("PeakMiner request %s %s failed: %s", method, url, err)
        return _Resp(None, None, None)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _d(obj: Any) -> dict | None:
        return obj if isinstance(obj, dict) else None

    @staticmethod
    def _l(obj: Any) -> list:
        return obj if isinstance(obj, list) else []

    @staticmethod
    def _raise(resp: _Resp, path: str) -> None:
        """Turn a 4xx from a write/read into a PeakMinerAPIError for callers."""
        if resp.ok:
            return
        if resp.status is None:
            raise PeakMinerAPIError("no response from miner (timeout/connection)",
                                    status=None, path=path)
        code = HTTP_STATUS_TO_NAME.get(resp.status, f"http_{resp.status}")
        msg = resp.error or f"PeakMiner {code} ({resp.status})"
        raise PeakMinerAPIError(msg, status=resp.status, path=path)

    # ------------------------------------------------------------- reads (no raise)
    async def health(self) -> dict | None:
        """GET /health — never authenticated."""
        return self._d((await self._send("get", "/health")).body)

    async def summary(self) -> dict | None:
        """GET /summary."""
        return self._d((await self._send("get", "/summary")).body)

    async def config(self) -> dict | None:
        """GET /config — enabled whenever any control level is on."""
        return self._d((await self._send("get", "/config")).body)

    async def control_state(self) -> dict | None:
        """GET /control/state — GPU pause reasons + current pool index/count."""
        return self._d((await self._send("get", "/control/state")).body)

    async def get_oc(self) -> list:
        """GET /control/oc — per-GPU OC profiles (needs the ``oc`` level)."""
        return await self._control_read("/control/oc")

    async def get_gpu_oc(self, gpu: int) -> dict | None:
        """GET /control/oc/{gpu}."""
        return await self._control_read(f"/control/oc/{gpu}")

    async def _control_read(self, path: str) -> Any:
        """Read-only, but raise on 4xx since these are control reads."""
        resp = await self._send("get", path)
        self._raise(resp, path)
        return resp.body

    # ------------------------------------------------------------- writes (raise)
    async def pause_gpus(self, gpus: Iterable[int] | None = None) -> list:
        """POST /control/pause. ``gpus=None`` -> all cards."""
        body = {} if gpus is None else {"gpus": [int(g) for g in gpus]}
        resp = await self._send("post", "/control/pause", body)
        self._raise(resp, "/control/pause")
        return self._l(resp.body)

    async def resume_gpus(self, gpus: Iterable[int] | None = None) -> list:
        """POST /control/resume. ``gpus=None`` -> all cards."""
        body = {} if gpus is None else {"gpus": [int(g) for g in gpus]}
        resp = await self._send("post", "/control/resume", body)
        self._raise(resp, "/control/resume")
        return self._l(resp.body)

    async def switch_pool(self, index: int | None = None) -> Any:
        """POST /control/pool. ``index`` -> absolute; ``None`` -> next (l hotkey)."""
        if index is None:
            resp = await self._send("post", "/control/pool", {"action": "next"})
        else:
            resp = await self._send("post", "/control/pool", {"index": int(index)})
        self._raise(resp, "/control/pool")
        return resp.body

    async def set_config(self, fields: dict[str, Any]) -> Any:
        """PUT /config for coin/pools/user/worker. Returns 202 -> {restart,...}."""
        resp = await self._send("put", "/config", fields)
        self._raise(resp, "/config")
        return resp.body

    async def delete_config_overlay(self, part: str | None = None) -> Any:
        """DELETE /config/overlay.

        ``part=None`` -> back to the flight sheet; ``part="config"`` removes only
        the config part; ``part="oc"`` removes only the OC part (no restart).
        """
        path = "/config/overlay"
        if part is not None:
            path = f"/config/overlay?part={part}"
        resp = await self._send("delete", path)
        self._raise(resp, path)
        return resp.body

    async def patch_oc(self, gpu: int, fields: dict[str, Any]) -> Any:
        """PATCH /control/oc/{gpu} — merge knobs of the API's own profile."""
        resp = await self._send("patch", f"/control/oc/{gpu}", fields)
        self._raise(resp, f"/control/oc/{gpu}")
        return resp.body

    async def put_oc(self, gpu: int, fields: dict[str, Any]) -> Any:
        """PUT /control/oc/{gpu} — replace the whole profile for one GPU."""
        resp = await self._send("put", f"/control/oc/{gpu}", fields)
        self._raise(resp, f"/control/oc/{gpu}")
        return resp.body

    async def set_gpu_power(self, gpu: int, percent: float) -> Any:
        """Set a GPU power limit as a percentage of the card's own default.

        ``percent`` is 0-100 and is sent to the miner as ``"<percent>%"`` (e.g.
        ``"75%"``); raw watts would be the numeric form. The driver applies the
        value in place and answers with ``applied`` / ``skipped`` lists.

        PATCH merges a single knob into a profile the API itself set, but answers
        ``409`` when the profile source is ``cli`` / ``bench`` - so in that case
        PUT the whole profile back with the new power merged in. Either way the
        remaining knobs (core / mem / fan) are left alone.
        """
        gpus = await self.get_oc() or []
        entry = next(
            (g for g in gpus if isinstance(g, dict) and g.get("id") == int(gpu)),
            None,
        )
        source = entry.get("source") if isinstance(entry, dict) else None
        profile = entry.get("profile") if isinstance(entry, dict) else None
        fields: dict[str, Any] = {"power": f"{float(percent):g}%"}
        if source == "api":
            return await self.patch_oc(int(gpu), fields)
        merged: dict[str, Any] = dict(profile) if isinstance(profile, dict) else {}
        merged.update(fields)
        return await self.put_oc(int(gpu), merged)

    async def restore_gpu_oc(self, gpu: int) -> Any:
        """DELETE /control/oc/{gpu} — back to the command-line flags / stock."""
        resp = await self._send("delete", f"/control/oc/{gpu}")
        self._raise(resp, f"/control/oc/{gpu}")
        return resp.body


def _try_json(text: str) -> Any:
    """Lenient JSON decode of a non-strict response body."""
    try:
        return json_loads(text)
    except Exception:  # noqa: BLE001
        return None
