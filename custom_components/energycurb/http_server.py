"""HTTP listener that receives /v3/samples/<serial> POSTs from Curb hubs."""
from __future__ import annotations

import json
import logging
import zlib
from typing import Any

import msgpack
from aiohttp import web

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_CIRCUITS,
    CONF_DEVICES,
    DOMAIN,
    NUM_CIRCUITS,
    SIGNAL_NEW_DEVICE,
    SIGNAL_UPDATE_FMT,
    WH_PER_SEC_TO_W,
)
from .hub_config import build_hub_config, default_circuits

_LOGGER = logging.getLogger(__name__)


class EnergyCurbHttpServer:
    """aiohttp server bound on a user-chosen host/port that sinks Curb samples."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
        port: int,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.host = host
        self.port = port
        self.serials: set[str] = set()
        self.latest: dict[str, dict[int, float]] = {}
        self.latest_timestamp: dict[str, int] = {}
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None

    async def async_start(self) -> None:
        app = web.Application()
        app.router.add_post("/v3/samples/{serial}", self._handle_samples)
        app.router.add_get("/v3/hub_config/{serial}", self._handle_hub_config)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self.host, port=self.port)
        await self._site.start()
        _LOGGER.info(
            "EnergyCurb listening on %s:%d (POST /v3/samples/<serial>, "
            "GET /v3/hub_config/<serial>)",
            self.host,
            self.port,
        )

    async def async_stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def circuits_for(self, serial: str) -> list[dict[str, Any]]:
        """Return the 18-circuit config for `serial`, or defaults if absent."""
        devices = self.entry.options.get(CONF_DEVICES, {})
        cfg = devices.get(serial, {}).get(CONF_CIRCUITS)
        if cfg and len(cfg) == NUM_CIRCUITS:
            return cfg
        return default_circuits()

    async def _handle_samples(self, request: web.Request) -> web.Response:
        serial = request.match_info["serial"]
        raw = await request.read()

        data = raw
        if request.headers.get("Content-Encoding", "").lower() == "deflate":
            try:
                data = zlib.decompress(raw)
            except zlib.error:
                try:
                    data = zlib.decompress(raw, -zlib.MAX_WBITS)
                except zlib.error as err:
                    _LOGGER.warning("deflate error from %s: %s", serial, err)
                    return web.Response(status=400, text="bad deflate body")

        try:
            payload = msgpack.unpackb(data, raw=False, strict_map_key=False)
        except Exception as err:  # msgpack raises several distinct types
            _LOGGER.warning("msgpack error from %s: %s", serial, err)
            return web.Response(status=400, text="bad msgpack body")

        samples = payload.get("s", []) if isinstance(payload, dict) else []
        samples = sorted(samples, key=lambda s: s.get("t", 0))
        for sample in samples:
            self._apply_sample(serial, sample)

        return web.Response(
            status=200,
            content_type="application/json",
            text='{"messages":0}',
        )

    async def _handle_hub_config(self, request: web.Request) -> web.Response:
        serial = request.match_info["serial"]
        body = build_hub_config(serial, self.circuits_for(serial))
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(body, indent=4),
        )

    def _apply_sample(self, serial: str, sample: dict[str, Any]) -> None:
        watts: list[float] = []
        for group in sample.get("g", []):
            for ch in group.get("c", []):
                w_wh = ch.get("w") or 0
                watts.append(abs(w_wh) * WH_PER_SEC_TO_W)
        if len(watts) != NUM_CIRCUITS:
            _LOGGER.debug(
                "%s: expected %d circuits, got %d — skipping",
                serial,
                NUM_CIRCUITS,
                len(watts),
            )
            return

        store = self.latest.setdefault(serial, {})
        for i, w in enumerate(watts):
            store[i] = w
        if (t := sample.get("t")) is not None:
            self.latest_timestamp[serial] = t

        if serial not in self.serials:
            self.serials.add(serial)
            async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, serial)

        async_dispatcher_send(
            self.hass, SIGNAL_UPDATE_FMT.format(serial=serial)
        )
