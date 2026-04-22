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


def _try_msgpack(data: bytes) -> Any:
    """Return the decoded MessagePack object, or None if data isn't msgpack."""
    try:
        return msgpack.unpackb(data, raw=False, strict_map_key=False)
    except Exception:
        return None


def _pending_key(entry: ConfigEntry) -> str:
    return f"_pending_messages_{entry.entry_id}"


def _pending_messages(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, list[dict[str, Any]]]:
    """Per-entry outbound message queue, keyed by hub serial.

    Lives in hass.data (not on the server instance) so enqueued messages
    survive integration reloads — options changes trigger a reload, and
    the notification we enqueue just before saving has to still be there
    when the new server comes up to answer the hub's next poll.
    """
    return hass.data.setdefault(DOMAIN, {}).setdefault(_pending_key(entry), {})


def enqueue_hub_message(
    hass: HomeAssistant,
    entry: ConfigEntry,
    serial: str,
    message: dict[str, Any],
) -> None:
    """Queue `message` to hand back to `serial` on its next /v3/messages GET."""
    _pending_messages(hass, entry).setdefault(serial, []).append(message)


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
        app.router.add_get("/v3/messages/{serial}", self._handle_messages)
        app.router.add_post("/v3/messages/{serial}", self._handle_messages_post)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self.host, port=self.port)
        await self._site.start()
        _LOGGER.info(
            "EnergyCurb listening on %s:%d "
            "(POST /v3/samples/<serial>, "
            "GET /v3/hub_config/<serial>, "
            "GET/POST /v3/messages/<serial>)",
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
        data = await request.read()

        # aiohttp auto-inflates Content-Encoding: deflate bodies in current
        # releases, so `data` is usually already the MessagePack payload.
        # Older aiohttp (or a proxy that passes the body through verbatim)
        # hands us the still-compressed bytes, so fall back to an explicit
        # inflate when the first decode fails.
        payload = _try_msgpack(data)
        if payload is None:
            for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
                try:
                    inflated = zlib.decompress(data, wbits)
                except zlib.error:
                    continue
                payload = _try_msgpack(inflated)
                if payload is not None:
                    break

        if payload is None:
            _LOGGER.warning(
                "undecodable body from %s (%d bytes, Content-Encoding=%r)",
                serial,
                len(data),
                request.headers.get("Content-Encoding"),
            )
            return web.Response(status=400, text="bad body")

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
        # request.host carries the Host header verbatim (hostname plus
        # port if the hub used a non-default one), so echoing it back
        # produces endpoints that are guaranteed to be reachable — even
        # through a reverse proxy or iptables redirect.
        base_url = f"{request.scheme}://{request.host}"
        body = build_hub_config(
            serial, self.circuits_for(serial), base_url=base_url
        )
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(body, indent=4),
        )

    async def _handle_messages(self, request: web.Request) -> web.Response:
        """Serve the hub's every-5s message poll.

        `?get_count=true` → {"number_of_available_hub_messages": N}.
        No query → dequeue one message body, or 404 if the queue is empty
        (HubMessaging treats any non-200 as "stop polling").
        """
        serial = request.match_info["serial"]
        queue = _pending_messages(self.hass, self.entry).setdefault(serial, [])

        if request.query.get("get_count") == "true":
            return web.Response(
                status=200,
                content_type="application/json",
                text=json.dumps({"number_of_available_hub_messages": len(queue)}),
            )

        if not queue:
            return web.Response(status=404, text="no messages")

        message = queue.pop(0)
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(message),
        )

    async def _handle_messages_post(self, request: web.Request) -> web.Response:
        # Hub-to-server messages (diagnostics, state, etc.). We don't
        # consume them yet — just acknowledge with 201 so the hub
        # doesn't log the post as failed.
        return web.Response(status=201)

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
