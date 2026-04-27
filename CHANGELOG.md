# Changelog

All notable changes to this project are documented here. Versions
follow [semantic versioning](https://semver.org/), and the format is
loosely based on [Keep a Changelog](https://keepachangelog.com/).

## [1.1.0] — 2026-04-26

### Highlights
- **Lite (12-channel) hubs are now supported** alongside standard
  18-channel hubs. The integration auto-detects the channel count from
  the hub's first samples POST and creates the right number of sensors.
- **Per-hub sample period** is now configurable from the options flow
  (1–60 s). Power readings now scale correctly when the period is
  longer than 1 s.
- **New `ROGOWSKI80100` clamp option** for Rogowski flexible coils on
  standard hubs' last two ADE chips (positions C1–C6).
- **`/v3/diagnostics` endpoint** is now answered (was returning 404,
  cluttering `streamer.log`).

### Added
- Auto-detect per-hub chip layout (`[6,6,3,3]` standard, `[6,6]` Lite)
  from the first samples POST. Persisted across restarts; the options
  form auto-sizes to match.
- Per-hub **Sample period (seconds)** option, 1–60, default 1.
  Written into `sampling.sample_period_ms` on the next hub-config
  fetch. **Only the 1 second option seems to work**
- **Rogowski 80/100 A** clamp choice in the dropdown. Use it on bus
  bars, bundled service-entrance cable, tight enclosures, parallel
  conductors, or any feed where a split-core CT can't physically fit.
  See the README for hub-position constraints.
- `/v3/diagnostics` (and `/v3/diagnostics/{serial}`) now return
  `200 {"messages":0}` — same shape as `/v3/samples` — so the hub
  stops logging failed POSTs.

### Changed
- **Power calculation now divides by the configured sample period.**
  Previously assumed a 1 s sampling interval and reported wrong
  wattages for any other period (`P = w × 3600 / T`).
- **Clamp dropdown labels** now include the Curb part number alongside
  the rating (`30 A (XIAMEN30)`, `Rogowski 80/100 A (ROGOWSKI80100)`,
  etc.) so they line up with what's printed in your `hub-config.json`
  backup.
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
- **It appears that only specific sample periods work.** Use 1 second
  unless you know that your hub needs a different value, or works with
  a different value. This setting is provided only because it is an
  option in the hub-config.json file.
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
