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
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CIRCUITS,
    CONF_CIRCUIT_BIDIRECTIONAL,
    CONF_DEVICES,
    CONF_SAMPLE_PERIOD_S,
    DEFAULT_CHIP_CHANNELS,
    DEFAULT_SAMPLE_PERIOD_S,
    DOMAIN,
    SIGNAL_NEW_DEVICE,
    SIGNAL_UPDATE_FMT,
    WH_PER_SEC_TO_W,
)
from .hub_config import build_hub_config, default_circuits

_LOGGER = logging.getLogger(__name__)

# Chip layouts we accept from a samples POST. Anything else is treated
# as a malformed payload — we refuse to learn an unknown shape (which
# would resize the sensor list to whatever garbage the hub just sent)
# and skip the sample.
_KNOWN_LAYOUTS: tuple[tuple[int, ...], ...] = (
    (6, 6, 3, 3),  # Standard 4-chip hubs (00613, 00614, 00614_*, 00624)
    (6, 6),        # Lite 2-chip hubs (00615, 00619, 00625)
)


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


# --- energy-total persistence --------------------------------------------
# Per-entry Store holding {serial: {circuit_idx: wh_total}}. Kept off the
# recorder path — a dedicated JSON file is simpler and guarantees the
# counter survives even if the recorder DB is wiped.

ENERGY_STORAGE_VERSION = 1
ENERGY_SAVE_DELAY_SECS = 30  # batch up to 30s of samples into one disk write


def _energy_storage_key(entry: ConfigEntry) -> str:
    return f"{DOMAIN}.{entry.entry_id}.energy"


class CurbHttpServer:
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
        # Cumulative energy per (serial, circuit_idx), in Wh. The common
        # `energy_wh` store always backs the "Energy" sensor — its
        # meaning shifts with the circuit's bidirectional flag:
        #   non-bidirectional → Σ |w|
        #   bidirectional     → Σ max(w, 0)   ("consumption")
        # When the circuit is bidirectional, `energy_wh_production`
        # accumulates Σ max(-w, 0) and backs a separate "Energy
        # Production" sensor. Both stores are monotonic to satisfy HA's
        # total_increasing semantics.
        self.energy_wh: dict[str, dict[int, float]] = {}
        self.energy_wh_production: dict[str, dict[int, float]] = {}
        # Per-hub ADE chip layout, learned from the first samples POST
        # we see. Persisted alongside the energy counters so a Lite hub
        # gets the right number of sensors recreated across HA restarts.
        self.chip_channels: dict[str, list[int]] = {}
        self._energy_store: Store = Store(
            hass, ENERGY_STORAGE_VERSION, _energy_storage_key(entry)
        )
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None

    async def async_start(self) -> None:
        # Restore the persisted energy counters before we accept any
        # requests, so the first POST's delta is added to the right base
        # value (and the sensors come up with their last-saved totals).
        #
        # Storage shape evolved across versions without a version bump:
        #   pre-bidirectional : flat {serial: {idx: wh}} of |w| totals
        #   pre-multi-model   : {"energy","production": {serial: {idx: wh}}}
        #   current           : adds {"chip_channels": {serial: [n,...]}}
        # Detect by the top-level keys and map flat data into `energy`.
        stored = await self._energy_store.async_load()
        if stored:
            if "energy" in stored or "production" in stored:
                energy_data = stored.get("energy", {})
                production_data = stored.get("production", {})
                chip_data = stored.get("chip_channels", {})
            else:
                energy_data = stored
                production_data = {}
                chip_data = {}
            for target, data in (
                (self.energy_wh, energy_data),
                (self.energy_wh_production, production_data),
            ):
                for serial, circuits in data.items():
                    target[serial] = {
                        int(k): float(v) for k, v in circuits.items()
                    }
                    self.serials.add(serial)
            for serial, layout in chip_data.items():
                if isinstance(layout, list) and layout:
                    self.chip_channels[serial] = [int(n) for n in layout]

        app = web.Application()
        app.router.add_post("/v3/samples/{serial}", self._handle_samples)
        app.router.add_get("/v3/hub_config/{serial}", self._handle_hub_config)
        app.router.add_get("/v3/messages/{serial}", self._handle_messages)
        app.router.add_post("/v3/messages/{serial}", self._handle_messages_post)
        app.router.add_route("*", "/v3/diagnostics", self._handle_diagnostics)
        app.router.add_route(
            "*", "/v3/diagnostics/{serial}", self._handle_diagnostics
        )
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self.host, port=self.port)
        await self._site.start()
        _LOGGER.info(
            "Curb listening on %s:%d "
            "(POST /v3/samples/<serial>, "
            "GET /v3/hub_config/<serial>, "
            "GET/POST /v3/messages/<serial>)",
            self.host,
            self.port,
        )

    async def async_stop(self) -> None:
        # Flush any pending energy-counter writes synchronously so a
        # clean unload/reload doesn't lose the last ≤30s of accumulation.
        await self._energy_store.async_save(self._energy_snapshot())
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def chip_channels_for(self, serial: str) -> list[int]:
        """Return the per-chip channel layout for `serial`.

        Falls back to the standard 4-chip layout until we've seen a
        samples POST that tells us otherwise. Returns a fresh list so
        callers can't accidentally mutate the live server state.
        """
        stored = self.chip_channels.get(serial)
        return list(stored) if stored else list(DEFAULT_CHIP_CHANNELS)

    def num_circuits_for(self, serial: str) -> int:
        """Total channel count for `serial`."""
        return sum(self.chip_channels_for(serial))

    def circuits_for(self, serial: str) -> list[dict[str, Any]]:
        """Per-circuit config for `serial`, sized to its detected layout.

        Pads with defaults if the saved options are too short, truncates
        if they're too long — so a hub that switches model (or loads
        legacy 18-entry options under a Lite layout) still works.
        """
        n = self.num_circuits_for(serial)
        devices = self.entry.options.get(CONF_DEVICES, {})
        cfg = devices.get(serial, {}).get(CONF_CIRCUITS)
        if cfg and len(cfg) >= n:
            return cfg[:n]
        defaults = default_circuits(n)
        if cfg:
            return list(cfg) + defaults[len(cfg):]
        return defaults

    def sample_period_for(self, serial: str) -> int:
        """Return the configured sample period (whole seconds, ≥ 1)."""
        devices = self.entry.options.get(CONF_DEVICES, {})
        val = devices.get(serial, {}).get(CONF_SAMPLE_PERIOD_S)
        if val is None:
            return DEFAULT_SAMPLE_PERIOD_S
        try:
            return max(1, int(round(float(val))))
        except (TypeError, ValueError):
            return DEFAULT_SAMPLE_PERIOD_S

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
        # The hub multiplexes two kinds of sample on this endpoint:
        #   p == 1            raw 1-second readings (w is signed Wh/1s)
        #   p == 60           1-minute aggregates (w is signed Wh/60s)
        #   p in {300,3600,86400}  5-min / 1-hr / 1-day rollups of the
        #                          same 1-minute data — using them
        #                          would double-count, so we drop them.
        #
        # `w` is in Wh for both kinds; the period only affects the
        # Wh→W conversion when deriving power. Energy accumulation
        # reads exclusively from 1-minute aggregates (one Wh delta per
        # minute → one HA state write per minute, vs one per second
        # from the raw stream — ~60x less recorder pressure with no
        # impact on long-term statistics). Power tracks whichever
        # source matches the user-configured sample period: raw at
        # 1 Hz when sample_period_s == 1, the 1-minute aggregate
        # otherwise.
        use_raw_power = self.sample_period_for(serial) == 1
        skipped = 0
        for sample in samples:
            p = int(sample.get("p") or 1)
            if p == 1:
                self._apply_sample(
                    serial,
                    sample,
                    update_power=use_raw_power,
                    update_energy=False,
                )
            elif p == 60:
                self._apply_sample(
                    serial,
                    sample,
                    update_power=not use_raw_power,
                    update_energy=True,
                )
            else:
                skipped += 1
        if skipped:
            _LOGGER.debug(
                "%s: skipped %d coarser-aggregate sample(s) "
                "(5-min/1-hr/1-day rollups)",
                serial,
                skipped,
            )

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
            serial,
            self.circuits_for(serial),
            base_url=base_url,
            sample_period_s=self.sample_period_for(serial),
            chip_channels=self.chip_channels_for(serial),
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

    async def _handle_diagnostics(self, request: web.Request) -> web.Response:
        # Stub: any method/path on /v3/diagnostics gets the same 200 +
        # `{"messages":0}` body the samples endpoint returns, so the
        # hub's diagnostics POSTs don't show up as failed in streamer.log.
        return web.Response(
            status=200,
            content_type="application/json",
            text='{"messages":0}',
        )

    def _apply_sample(
        self,
        serial: str,
        sample: dict[str, Any],
        *,
        update_power: bool,
        update_energy: bool,
    ) -> None:
        """Ingest one sample, optionally updating power and/or energy.

        The channel-level `w` field is signed Wh over the sample's `p`
        seconds for both raw and aggregated samples — the period only
        affects how Wh maps to instantaneous power, not the Wh value
        itself.
          power      = w × 3600 / p  (Wh/p seconds → W)
          energy Δ   = w             (sum the Wh directly)

        Energy is only ever updated from the 1-minute aggregate stream
        (gates the per-channel store at 1 update/min instead of 1/sec
        — ~60× less recorder pressure).

        Bi-directional caveat: aggregates report the period's *net*
        signed Wh, so a circuit that imports and exports in equal
        measure within a single minute nets to zero and contributes no
        delta to either consumption or production. Slow-moving feeds
        (typical solar/grid mains) are unaffected; fast-flipping loads
        lose sub-minute fidelity in the energy counters.

        Layout detection happens on every sample type, so the chip
        layout is learned even if neither power nor energy is updated.
        """
        groups = sample.get("g", []) or []
        sample_w: list[float] = []
        layout: list[int] = []
        for group in groups:
            channels = group.get("c", []) or []
            layout.append(len(channels))
            for ch in channels:
                w = ch.get("w") or 0
                sample_w.append(float(w))
        if not sample_w:
            _LOGGER.debug("%s: empty sample — skipping", serial)
            return

        if tuple(layout) not in _KNOWN_LAYOUTS:
            _LOGGER.warning(
                "%s: unknown chip layout %s (expected one of %s) — "
                "skipping sample",
                serial,
                layout,
                [list(known) for known in _KNOWN_LAYOUTS],
            )
            return

        prior_layout = self.chip_channels.get(serial)
        if prior_layout != layout:
            if prior_layout is not None:
                _LOGGER.info(
                    "%s: chip layout changed %s -> %s",
                    serial,
                    prior_layout,
                    layout,
                )
            self.chip_channels[serial] = layout

        period_s = max(1, int(sample.get("p") or 1))
        circuits = self.circuits_for(serial)

        if update_power:
            # w is Wh over `period_s` seconds → average W = w × 3600 / p.
            power_store = self.latest.setdefault(serial, {})
            for i, w in enumerate(sample_w):
                power_store[i] = w * WH_PER_SEC_TO_W / period_s

        if update_energy:
            # w is the period's signed Wh delta — accumulate directly.
            energy_store = self.energy_wh.setdefault(serial, {})
            production_store = self.energy_wh_production.setdefault(serial, {})
            for i, w in enumerate(sample_w):
                if circuits[i].get(CONF_CIRCUIT_BIDIRECTIONAL):
                    if w > 0:
                        energy_store[i] = energy_store.get(i, 0.0) + w
                    elif w < 0:
                        production_store[i] = (
                            production_store.get(i, 0.0) - w
                        )
                else:
                    energy_store[i] = energy_store.get(i, 0.0) + abs(w)

        if (t := sample.get("t")) is not None:
            self.latest_timestamp[serial] = t

        if serial not in self.serials:
            self.serials.add(serial)
            async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, serial)

        async_dispatcher_send(
            self.hass, SIGNAL_UPDATE_FMT.format(serial=serial)
        )
        if update_energy:
            # Energy moved; debounce-persist. HA's Store flushes on
            # clean shutdown too, so worst-case loss is ~30s of agg
            # increments (one minute's worth).
            self._energy_store.async_delay_save(
                self._energy_snapshot, ENERGY_SAVE_DELAY_SECS
            )

    def _energy_snapshot(self) -> dict[str, Any]:
        return {
            "energy": self.energy_wh,
            "production": self.energy_wh_production,
            "chip_channels": self.chip_channels,
        }
