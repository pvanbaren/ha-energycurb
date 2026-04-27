<img src="icon.png" alt="" align="right" width="96" height="96">

# Curb — Home Assistant Integration

Receive live energy data directly from orphaned **Curb Energy Monitor** hubs
and surface it as native Home Assistant sensors — no MQTT, no polling.

Curb Inc. shut down its cloud in February 2026, orphaning every hub in the
field. After restoring root access (see
[curbed](https://github.com/codearranger/curbed)) and pointing the hub's
on-device `/data/hub-config.json` at your Home Assistant host, the hub
happily POSTs its samples to whatever answers there. This integration is
what answers.

## What it does

- Binds an HTTP listener on a user-configurable host/port.
- Accepts `POST /v3/samples/<serial>` from any number of Curb hubs.
- Decodes the `deflate` + MessagePack body.
- Creates two or three native `sensor.*` entities per circuit (so 36
  for an 18-channel standard hub, 24 for a 12-channel Lite hub, plus
  one extra per circuit flagged bi-directional). The channel count is
  auto-detected from the first samples POST:
  - **Power** (`device_class: power`, `state_class: measurement`, unit `W`)
    — instantaneous draw, derived as `|w| × 3600 / T`, where `T` is the
    configured sample period in seconds (default 1).
  - **Energy** (`device_class: energy`, `state_class: total_increasing`,
    unit `kWh`) — cumulative consumption. For non-bidirectional circuits
    it's `Σ |w|`; for bi-directional circuits it's `Σ max(w, 0)` so the
    counter stays monotonic. Persisted across restarts so long-term
    statistics and the Energy dashboard work out of the box.
  - **Energy Production** (same device/state class, only created for
    bi-directional circuits) — cumulative `Σ max(-w, 0)`, i.e. back-feed.
- Each hub appears as its own HA **device** identified by its serial.

`iot_class` is `local_push` — data arrives on every hub sample (~every
few seconds), not polled.

## Installation (HACS)

1. In HACS → Integrations → ⋯ → **Custom repositories**, add
   `https://github.com/pvanbaren/ha-energycurb` with category **Integration**.
2. Install **Curb** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → **Curb**.
4. Enter the bind host (`0.0.0.0` is fine) and port.

## Pointing your hub at this integration

> **Save a copy of `/data/hub-config.json` off the hub before the
> integration takes over — whichever path you follow below.** It's
> the only record of which physical clamp and voltage is wired to
> each channel position, and you'll need it to fill in the options
> flow (step 4). Once the integration starts answering the hub's
> config fetch, the on-device file gets regenerated on every poll and
> the original per-channel calibration is gone. Pull a copy with
> `scp root@<hub>:/data/hub-config.json ./hub-config.json.orig` (or
> equivalent) and keep it somewhere durable.

> **Automated alternative:** the companion
> [`ha-curb-update-server`](https://github.com/pvanbaren/ha-curb-update-server)
> integration impersonates Curb's firmware update endpoint (point
> `updates.energycurb.com` at your HA host via your DNS server) and
> serves a payload that both roots the hub and rewrites its endpoints
> to `homeassistant.local:8989` on the next hourly update check. With
> that in place you can skip steps 1 and 3 below — no manual rooting,
> no hand-editing `/data/hub-config.json` — but still grab the backup
> from step 2 once the payload has enabled SSH, then jump to step 4 to
> map the circuits in the options flow.

1. Gain root access to the hub using
   [codearranger/curbed](https://github.com/codearranger/curbed). That
   project walks through unlocking the hub and getting a shell on it.
2. **Back up `/data/hub-config.json` before you touch it** (see the
   callout above; e.g. `cp /data/hub-config.json /data/hub-config.json.orig`
   plus a copy pulled off the hub onto your workstation). You'll mine
   it for per-circuit settings in step 4 — read each channel's
   `clamp_definition_id` to pick the clamp, and compare its
   `i_multiplier` to the canonical 110 V value for that clamp to
   decide the voltage (a ~2× magnitude means 220 V) and polarity
   (negative means the clamp is inverted).
3. On the hub, edit `/data/hub-config.json` and change every URL under
   the `endpoints` block so the host and port match your Home Assistant
   listener. For a default install that's `http://<ha-host>:8989`:
   ```json
   "endpoints": {
       "hub_config":  "http://<ha-host>:8989/v3/hub_config",
       "messages":    "http://<ha-host>:8989/v3/messages",
       "samples":     "http://<ha-host>:8989/v3/samples",
       "diagnostics": "http://<ha-host>:8989/v3/diagnostics"
   },
   ```
   Replace `<ha-host>` with your HA server's IP or hostname and `8989`
   with the port you picked in the integration setup. Bump the
   `revision` field by one so the streamer re-reads the file on its
   next poll, then restart the hub (or just the `streamer` service).
4. Open the integration's options flow (Settings → Devices & services
   → Curb → Configure). Enter the hub's sample period (1–60 seconds;
   default 1), then for each channel position the hub reports (the
   form auto-sizes to whatever the hub actually has — 18 for a
   standard 4-chip hub, 12 for a Lite 2-chip hub) set the clamp
   (`XIAMEN100` / `XIAMEN50` / `XIAMEN30` / `ROGOWSKI80100`), voltage
   (110V / 220V), inverted flag, and the bi-directional flag using your
   backup of the original `hub-config.json` as reference — match the
   `clamp_definition_id` and the sign/magnitude of the multipliers.

From then on the hub posts samples directly to this integration and
fetches its own future configs from `/v3/hub_config/<serial>` — which
this integration (re)generates on demand from the per-circuit settings
in the options flow, overwriting what you just hand-edited. Your edits
to the `endpoints` block are preserved on each regeneration because the
integration echoes back whatever host:port the hub used to reach it.

### Keeping the hub's original hub-config.json

If you'd rather keep the factory per-channel calibration on the hub
untouched and not have the integration regenerate anything, point the
`hub_config` endpoint at an address that will never respond — e.g. a
blackhole route or a reserved loopback like `http://127.0.0.1:1`:

```json
"endpoints": {
    "hub_config":  "http://127.0.0.1:1/v3/hub_config",
    "messages":    "http://<ha-host>:8989/v3/messages",
    "samples":     "http://<ha-host>:8989/v3/samples",
    "diagnostics": "http://<ha-host>:8989/v3/diagnostics"
},
```

The streamer's every-5-minute config poll will fail silently and the
hub keeps using whatever is already in `/data/hub-config.json`. Samples,
messages and diagnostics still flow to this integration, so the 18
sensor entities work normally — you just won't be able to change the
clamp/voltage mapping from the HA options flow.

## How devices appear

On the first POST from a serial, the integration:

1. Registers an HA device `Curb <serial>` (manufacturer: Curb).
2. Detects the hub's chip layout from the sample (e.g. `[6,6,3,3]` for
   a standard 4-chip hub → 18 channels, `[6,6]` for a Lite 2-chip hub
   → 12 channels) and attaches one power + one energy sensor per
   channel, plus one extra "Energy Production" sensor for any circuit
   flagged bi-directional. With default circuit names (`A1`…`A6`,
   `B1`…`B6`, optionally `C1`…`C6` on standard hubs), entity IDs come
   out as:
   ```
   sensor.curb_<serial>_a1              sensor.curb_<serial>_a1_energy
   sensor.curb_<serial>_a2              sensor.curb_<serial>_a2_energy
   …                                     …
   sensor.curb_<serial>_c6              sensor.curb_<serial>_c6_energy
   ```
   And for each bi-directional circuit (e.g. the mains backfed by
   solar):
   ```
   sensor.curb_<serial>_a1_energy_production
   ```
   The `entity_id` is HA's slug of the friendly name (so `A1` →
   `sensor.curb_<serial>_a1`), but the underlying `unique_id` uses the
   fixed 1-indexed circuit position so it stays stable across renames:
   `curb_<serial>_circuit_N` for power sensors,
   `curb_<serial>_circuit_N_energy` for consumption energy, and
   `curb_<serial>_circuit_N_energy_production` for production energy.
   Renames and dashboard placements survive reloads and restarts even
   if you change the circuit's friendly name in Configure.

After a reload the power sensors show `unavailable` until the hub's
next POST; the energy sensors come back immediately with their last
persisted total. Long-term statistics are preserved through restarts
and reloads.

## Circuit configuration

Each hub exposes its channels in a fixed physical order: a standard
4-chip hub has 18 channels grouped 6, 6, 3, 3; a Lite 2-chip hub has
12 channels grouped 6, 6. The exact count is auto-detected from the
hub's first samples POST. From **Settings → Devices & services → Curb
→ Configure**, a single form holds one per-hub field at the top
followed by one section per detected channel:

**Per hub**

- **Sample period (seconds)** — integer, 1–60, default 1. Written into
  the generated hub-config.json as `sampling.sample_period_ms`
  (`period × 1000`). Larger values *should* reduce POST frequency and
  log volume at the cost of time resolution. **In practice only the
  default 1-second value is known to work reliably** — other periods
  appear to be ignored or mishandled by the streamer firmware. The
  field is exposed because the underlying hub-config option exists;
  leave it at `1` unless you have evidence your hub honors a different
  value.

**Per circuit** (A1 … C6)

- **Name** — shown as the sensor's friendly name in HA. Defaults run
  A1–A6, B1–B6, C1–C6.
- **Current clamp** — one of:
  - `XIAMEN30` — 30 A Xiamen split-core CT (good for individual
    branch circuits up to about 24 A continuous).
  - `XIAMEN50` — 50 A Xiamen split-core CT (mid-size branch circuits,
    sub-panels under ~40 A).
  - `XIAMEN100` — 100 A Xiamen split-core CT (mains feeders or large
    sub-panels in a typical residential install).
  - `ROGOWSKI80100` — 80/100 A Rogowski flexible coil. Use when a
    split-core CT can't physically fit around the conductor (large
    bus bars, bundled service-entrance cable, tight panel
    enclosures), or when you need to clamp around multiple parallel
    conductors. Rogowski coils have no iron core, so they don't
    saturate at high currents and can wrap awkward shapes.

  **Where they can go on the hub:** only the last two ADE chips
  (positions C1–C6 on a standard 18-channel hub) support Rogowski
  sensing in firmware — positions A1–A6 and B1–B6 are wired for CT
  clamps only and will misread if you assign a Rogowski option to
  them. Lite 2-chip hubs (00615 / 00619 / 00625) don't have the
  Rogowski-capable chips at all, so the option is exposed but not
  usable on those models.

  **Hub-model variants for Rogowski current rating:** the
  `ROGOWSKI80100` multipliers in this integration target the 100 A
  Rogowski variant (Curb model `00614`). Curb also shipped 600 A
  (`00614_600`) and 1000 A (`00614_1000`) Rogowski hubs that use
  different ADE gain settings on chips 3/4 — those aren't currently
  served correctly; treat the option as 100 A only until a separate
  high-current variant is added.
- **Voltage** — `110V` or `220V`. Each voltage has its own production
  multiplier lookup; 220 V entries are copied verbatim from Curb's
  reference hub-config.json so the generated file matches byte-for-byte.
- **Inverted** — leave unchecked for a correctly-oriented clamp; check
  it to flip the sign of the channel's `i/w/var` multipliers.
- **Bi-directional** — check for circuits that can both import and
  export (e.g. a solar-backfed main). When set, the circuit gets a
  second "Energy Production" sensor and the "Energy" sensor only
  accumulates the positive side.

These values are compiled into a v3.1 `hub-config.json` and served at
`GET /v3/hub_config/<serial>`. The hub picks up changes on its next
5-second message poll (the integration queues a `{"type":"config"}`
hub message whenever you save, so there's no 5-minute wait for the
periodic refresh).

## Troubleshooting

- **Nothing shows up**: confirm the hub's `/data/hub-config.json` has
  your HA host:port in every `endpoints.*` URL, and check the hub's
  `/var/log/streamer.log` for POST responses. Check HA logs for
  `Curb listening on …`.
- **`Failed to bind … Address already in use`**: another service has the
  port. Pick a different one and reconfigure.
- **Entities stay unavailable**: the hub has reached you but its samples
  decoded to an empty channel list. Enable debug logging:
  ```yaml
  logger:
    logs:
      custom_components.energycurb: debug
  ```
- **Check the hub's own logs** in `/var/log/` on the device.
  `streamer.log` is the most useful when samples aren't reaching HA —
  it records the actual POSTs and their HTTP status codes.
  `sampler.log` shows ADE reads and per-sample errors on the way into
  the batch; `messages.log` covers the `/v3/messages` polling.

## License

MIT
