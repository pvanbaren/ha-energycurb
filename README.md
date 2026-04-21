# EnergyCurb — Home Assistant Integration

Receive live energy data directly from orphaned **Curb Energy Monitor** hubs
and surface it as native Home Assistant sensors — no MQTT, no polling.

Curb Inc. shut down its cloud in February 2026, bricking every hub in the
field. After restoring root access (see
[curbed](https://github.com/codearranger/curbed) or similar projects) and
redirecting `border.prod.energycurb.com` to your own server, the hub happily
POSTs its samples to whatever answers at that name. This integration is what
answers.

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
   `https://github.com/YOUR_USER/ha-energycurb` with category **Integration**.
2. Install **EnergyCurb** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → **EnergyCurb**.
4. Enter the bind host (`0.0.0.0` is fine) and port.

## Pointing your hub at this integration

The Curb hub is hard-coded to POST samples to
`https://border.prod.energycurb.com/v3/samples/<serial>` (falling back to
plain HTTP). You have to redirect that hostname locally:

- **Pi-hole / dnsmasq / your router DNS**:
  `border.prod.energycurb.com` → the IP of your Home Assistant host.
- The hub accepts any TLS certificate (`wget --no-check-certificate`), but
  if nothing is listening on :443 it transparently falls back to plain
  HTTP on :80, which this integration can handle directly.

### Dealing with privileged ports (80 / 443)

Home Assistant normally can't bind to ports below 1024. Two options:

- **Pick a high port** (default `8989`) and DNAT the real ports to it:
  ```
  iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8989
  ```
- **Reverse-proxy** `:80` (and optionally `:443`) through nginx/caddy/traefik
  to `http://<ha-host>:8989`.

The hub also pulls firmware-update manifests from
`updates.energycurb.com/api/firmware/...`. This integration does **not**
answer those — use [curbed](https://github.com/codearranger/curbed)'s
`serve.py` (or a merged server like `samples-to-mqtt.py` in the sibling
repo) for that one-shot unlock.

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

## Circuit names

The hub reports circuits in a fixed order with no labels. Rename the 18
entities per hub in the HA UI — those names persist. A future release may
accept a circuit-name map in the options flow.

## Troubleshooting

- **Nothing shows up**: check that `border.prod.energycurb.com` actually
  resolves to your HA host from the hub's network (`nslookup` on a
  device on the hub's subnet). Check HA logs for `EnergyCurb listening
  on …`.
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
