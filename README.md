<img src="icon.png" alt="" align="right" width="96" height="96">

# EnergyCurb — Home Assistant Integration

Receive live energy data directly from orphaned **Curb Energy Monitor** hubs
and surface it as native Home Assistant sensors — no MQTT, no polling.

Curb Inc. shut down its cloud in February 2026, bricking every hub in the
field. After restoring root access (see
[curbed](https://github.com/codearranger/curbed)) and pointing the hub's
on-device `/data/hub-config.json` at your Home Assistant host, the hub
happily POSTs its samples to whatever answers there. This integration is
what answers.

## What it does

- Binds an HTTP listener on a user-configurable host/port.
- Accepts `POST /v3/samples/<serial>` from any number of Curb hubs.
- Decodes the `deflate` + MessagePack body.
- Creates 18 native `sensor.*` entities (one per circuit) per hub, with
  `device_class: power`, `state_class: measurement`, unit `W`. These feed the
  HA Energy dashboard and long-term statistics out of the box.
- Each hub appears as its own HA **device** identified by its serial.

`iot_class` is `local_push` — data arrives on every hub sample (~every
few seconds), not polled.

## Installation (HACS)

1. In HACS → Integrations → ⋯ → **Custom repositories**, add
   `https://github.com/pvanbaren/ha-energycurb` with category **Integration**.
2. Install **EnergyCurb** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → **EnergyCurb**.
4. Enter the bind host (`0.0.0.0` is fine) and port.

## Pointing your hub at this integration

1. Gain root access to the hub using
   [codearranger/curbed](https://github.com/codearranger/curbed). That
   project walks through unlocking the hub and getting a shell on it.
2. **Back up `/data/hub-config.json` before you touch it** (e.g.
   `cp /data/hub-config.json /data/hub-config.json.orig` and pull a
   copy off the hub). The integration regenerates this file on the
   next config fetch and overwrites your per-channel calibration;
   the backup is the only record of which physical clamp and voltage
   is wired to each of the 18 positions, which you'll need to
   re-enter in the integration's options flow (clamp_definition_id
   and the 2× multiplier on 220V channels).
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
   → EnergyCurb → Configure) and, using your backup of the original
   `hub-config.json`, set the clamp (100A / 50A / 30A), voltage
   (110V / 220V) and polarity for each of the 18 positions to match
   the `clamp_definition_id` and multiplier values in the backup.

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
2. Attaches 18 sensors:
   ```
   sensor.circuit_1 … sensor.circuit_18
   ```
   with `unique_id = curb_<serial>_circuit_N`, so renames and dashboard
   placements survive reloads and restarts.

After a restart, sensors show `unavailable` until the hub's next POST
(usually seconds later). Long-term statistics are preserved.

## Circuit configuration

Each hub exposes 18 circuits in a fixed physical order (groups of 6, 6, 3, 3).
From **Settings → Devices & services → EnergyCurb → Configure**, assign per
circuit:

- **Name** — shown as the sensor's friendly name in HA. Defaults run
  A1–A6, B1–B6, C1–C6.
- **Current clamp** — `100A`, `50A`, or `30A` (Xiamen CT).
- **Voltage** — `110V` or `220V`. 220 V circuits get a 2× scale on
  `w_multiplier` / `var_multiplier` to compensate for the group's
  line-to-neutral voltage reference.
- **Polarity** — `+` for a correctly-oriented clamp, `-` to flip the sign
  of the channel's multipliers.

These values are compiled into a v3.1 `hub-config.json` and served at
`GET /v3/hub_config/<serial>`, so point your hub's config endpoint at this
integration (alongside the samples endpoint) and it will pull the config
on boot.

## Troubleshooting

- **Nothing shows up**: confirm the hub's `/data/hub-config.json` has
  your HA host:port in every `endpoints.*` URL, and check the hub's
  `/data/streamer.log` for POST responses. Check HA logs for
  `EnergyCurb listening on …`.
- **`Failed to bind … Address already in use`**: another service has the
  port. Pick a different one and reconfigure.
- **Entities stay unavailable**: the hub has reached you but its samples
  don't contain the expected 18 circuits. Enable debug logging:
  ```yaml
  logger:
    logs:
      custom_components.energycurb: debug
  ```

## License

MIT
