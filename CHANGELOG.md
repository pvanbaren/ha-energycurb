# Changelog

All notable changes to this project are documented here. Versions
follow [semantic versioning](https://semver.org/), and the format is
loosely based on [Keep a Changelog](https://keepachangelog.com/).

## [1.2.0] — 2026-04-29

### Highlights
- **New `Live Power Readings` switch** per Curb device — toggle the
  power-update interval (1 second ↔ 1 minute) without opening the
  options flow.

### Added
- `Live Power Readings` switch entity, attached to each hub device
  in the Configuration section. Toggling it writes the same
  `sample_period_s` field the options-flow dropdown writes (so the
  two stay in sync) and pushes a `{"type": "config"}` hub message
  so the new cadence takes effect within a few seconds rather than
  waiting up to 5 minutes for the hub's periodic config poll.

### Fixed
- **Power sensors no longer flip to `unavailable` on a reload.** The
  in-memory `latest` power store is now persisted alongside the
  energy counters, so a planned reload (options save, switch
  toggle, HA shutdown) restores the last-known W readings on the
  next start.

### Storage
- Persistence file gained a `"power"` top-level key alongside
  `"energy"` / `"production"` / `"chip_channels"`. Read path is
  additive-tolerant, so a downgrade to 1.1.0 just drops the new
  key and falls back to the prior "unavailable until next sample"
  behavior.

### Upgrade notes
- The new switch appears as a Configuration entity on each hub
  device, defaulting to ON (live mode = previous default behavior).

## [1.1.0] — 2026-04-26

### Highlights
- **Lite (12-channel) hubs are now supported** alongside standard
  18-channel hubs. The integration auto-detects the channel count
  from the hub's first samples POST and creates the right number of
  sensors.
- **Power and energy now ride separate sample streams.** Energy
  always accumulates from the hub's 1-minute aggregate (one HA
  state-write per minute, ~60× less recorder load); power tracks
  the raw 1 Hz stream by default, or the same aggregate when set to
  per-minute mode.
- **New clamp options:** `ROGOWSKI80100` (Rogowski flexible coil for
  high-current / awkward feeds) and `XIAMEN100THIN` (thin-profile
  100 A Xiamen CT, distinct calibration from `XIAMEN100`).
- **`/v3/diagnostics` endpoint** is now answered (was returning 404,
  cluttering `streamer.log`).
- **Hub no longer re-downloads `hub-config.json` every 5 minutes.**
  The `revision` field is now stable per server lifetime; the
  firmware only re-fetches when something has actually changed.

### Added
- Auto-detect per-hub chip layout (`[6,6,3,3]` standard, `[6,6]`
  Lite) from the first samples POST. Persisted across restarts; the
  options form auto-sizes to match.
- **Power update interval** option in the options flow — a 2-choice
  dropdown (`1 second` / `1 minute`) selecting which sample stream
  drives the power sensors. The chosen value is also written into
  `sampling.sample_period_ms` (1000 or 60000) on the next
  hub-config fetch, though the streamer firmware appears to ignore
  it on most hubs.
- `ROGOWSKI80100` clamp choice — Rogowski flexible coil for bus
  bars, bundled service-entrance cable, tight panel enclosures,
  parallel conductors, or any feed where a split-core CT can't
  physically fit. See the README for hub-position constraints.
- `XIAMEN100THIN` clamp choice — same 100 A rating as `XIAMEN100`,
  thin-profile body, distinct calibration. Pick this when your
  `hub-config.json` backup shows that ID. 220 V values are 2× the
  110 V multipliers, matching the rest of the Xiamen family.
- `/v3/diagnostics` (and `/v3/diagnostics/{serial}`) now return a
  `200` with the same body shape as `/v3/samples`, so the hub stops
  logging failed POSTs.
- `/v3/samples/<serial>` and `/v3/diagnostics/<serial>` response
  bodies now report the real outbound message-queue depth (replacing
  the hardcoded `{"messages": 0}`). Hubs that honor this hint drain
  the queue within one samples cycle (~1 s) instead of waiting up
  to 5 s for their regular `/v3/messages` poll.

### Changed
- **Energy accumulation moved off the raw 1 Hz stream onto the
  1-minute aggregate stream.** Energy sensors now write state once
  per minute instead of once per second — roughly 60× less recorder
  pressure with no impact on long-term statistics or the Energy
  dashboard. Per-minute Wh delta is `w × p`, where `w` is the
  aggregate's signed average Wh/sec rate and `p` is its 60-second
  period.
- **Power-reading source is selectable** via the new dropdown:
  `1 second` keeps live 1 Hz updates from the raw stream;
  `1 minute` takes power from the 1-minute aggregate (one
  state-write per minute) for users who want to slash recorder
  load without losing energy data.
- **Mixed sample types are now distinguished by the top-level `p`
  field.** Raw 1 s samples (`p == 1`) and 1-minute aggregates
  (`p == 60`) both carry signed Wh/sec rates; power is derived as
  `w × 3600` and energy delta as `w × p`. 5-minute / 1-hour / 1-day
  rollups are dropped to avoid double-counting. Previously the
  integration treated every sample as raw at fixed 1 s, which
  corrupted readings whenever an aggregate arrived.
- **`hub-config.json` `revision` is now stable** per server
  lifetime, so the streamer only re-fetches the config when a
  reload (options change, etc.) actually bumps it, instead of every
  5-minute config poll.
- **Clamp dropdown labels** now include the Curb part number
  alongside the rating (`30 A (XIAMEN30)`,
  `Rogowski 80/100 A (ROGOWSKI80100)`, etc.) so they line up with
  what's printed in your `hub-config.json` backup.
- **`DEFAULT_ENDPOINTS`** now include port `:8989`, matching the
  listener's default port and what the README has shown all along.
- **`manifest.json` version** bumped to `1.1.0`.

### Documentation
- New top-of-section callout: **save a copy of `/data/hub-config.json`
  off the hub before the integration takes over**, applied to both the
  manual and automated install paths.
- New section pointing at the companion
  [`ha-curb-update-server`](https://github.com/pvanbaren/ha-curb-update-server)
  integration as an alternative to manual rooting + endpoint editing.
- `unique_id` vs `entity_id` distinction explicitly called out (the
  slugified-name `entity_id` and the position-based `unique_id` are
  intentionally different).
- Each clamp option in the README now includes a one-line use-case
  hint, and `ROGOWSKI80100` has full guidance on when to use it, where
  it works on the hub, and which hub variants this release does *not*
  yet support.

### Known limitations
- **Bi-directional fidelity at sub-minute timescales is reduced.**
  Energy is accumulated from the hub's 1-minute aggregate, which
  carries the minute's *net* signed Wh. A circuit that imports and
  exports in equal measure within a single minute nets to zero and
  contributes no Wh delta in either direction. Slow-moving feeds
  (typical solar/grid mains) are unaffected; fast-flipping loads lose
  sub-minute fidelity in the energy counters.
- **`sampling.sample_period_ms` may be ignored by the streamer
  firmware.** The integration writes whatever you set, but the hub
  appears to keep emitting raw samples at 1 Hz regardless. The
  integration-side power-source switch (raw vs. 1-min aggregate) still
  works because it keys off the sample's own period field.
- **Lite-hub upgraders may have ghost entities.** If you ran the prior
  version against a Lite hub, the entity registry will have channels
  13–18 sitting unavailable. They aren't auto-cleaned; remove them
  manually via the entity registry UI. (A repair-flow surface for this
  is a candidate for a follow-up release.)
- **Rogowski hub variants beyond 100 A aren't supported.** The
  `ROGOWSKI80100` multipliers target Curb's 100 A variant (model
  `00614`). The 600 A (`00614_600`) and 1000 A (`00614_1000`) hubs use
  different ADE gain settings on chips 3/4 that this release doesn't
  emit. Treat the option as 100 A only.
- **Storage shape evolved without a version bump.** A `chip_channels`
  key was added to the persistence file alongside `energy` /
  `production`. Read path is additive-tolerant; downgrading to 1.0.0
  then re-upgrading would silently drop the field on the next
  debounced save (forcing a one-POST re-detection cycle). A formal
  `Store` version migration is on the follow-up list.

### Upgrade notes
- **Standard 18-channel hubs**: no action required. The default layout
  fallback matches your existing setup.
- **Lite 12-channel hubs**: after upgrading, expect 12 sensors to be
  created on the first POST. Old ghosts (sensors 13–18) need manual
  removal from Settings → Devices & Services → Curb → entity registry.
- **All hubs**: the new Sample period field defaults to `1`, preserving
  previous behavior. Increase it only if you want to reduce POST
  frequency / log volume.

## [1.0.0]

Initial tagged release. Receives live energy data directly from
orphaned Curb Energy Monitor hubs and surfaces it as native Home
Assistant sensors.

### Added
- **HTTP listener** bound on a user-configurable host/port that accepts
  `POST /v3/samples/<serial>` from any number of hubs. Decodes the
  `deflate` + MessagePack body and dispatches to the right device.
- **36 native sensors per hub** (one power + one energy per circuit on
  a standard 4-chip / 18-channel hub):
  - **Power** — `device_class: power`, `state_class: measurement`,
    unit `W`. Signed reading so direction-of-flow is preserved.
  - **Energy** — `device_class: energy`, `state_class:
    total_increasing`, unit `kWh`. Persisted across HA restarts so
    long-term statistics and the Energy dashboard work out of the box.
- **Bi-directional energy split.** Per-circuit toggle that produces a
  second "Energy Production" sensor for circuits that can both import
  and export (e.g. a solar-backfed main); the consumption counter then
  accumulates only `max(w, 0)` so it stays monotonic.
- **Hub-config.json generation** served at `GET /v3/hub_config/<serial>`,
  built from the per-circuit options. Production CT-clamp multipliers
  for `XIAMEN30` / `XIAMEN50` / `XIAMEN100` at 110 V and 220 V are
  copied byte-for-byte from Curb's reference configs so the generated
  file matches what the firmware originally shipped with.
- **Per-circuit options flow.** Settings → Devices & services → Curb →
  Configure exposes name, current clamp, voltage, and inverted flag
  per channel, grouped into expandable sections.
- **Hub-side message channel.** `GET /v3/messages/<serial>` returns
  queued hub messages, `POST` is acknowledged with 201. On options
  save, the integration enqueues a `{"type":"config"}` notification so
  the hub fetches the updated `hub-config.json` on its next 5-second
  poll instead of waiting up to 5 minutes for the periodic refresh.
- **Each hub registers as its own HA device** named `Curb <serial>`,
  identified by its hub serial.
- **`local_push` IoT class** — data arrives on every hub sample
  (~every few seconds), no polling.
- **README walkthrough** for rooting via
  [codearranger/curbed](https://github.com/codearranger/curbed),
  backing up the original `/data/hub-config.json`, redirecting
  endpoints to the integration, and reconstructing the per-channel
  clamp/voltage map from the backup.
