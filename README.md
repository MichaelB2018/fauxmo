# FauxMo — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that exposes selected entities as
Belkin WeMo switches on the local network. Alexa discovers and controls
them without cloud services or an Alexa skill — everything stays on
your LAN, **no port 80 required**.

Based on the [fauxmo](https://github.com/n8henrie/fauxmo) project by
Nathan Henrie, originally forked from
[makermusings/fauxmo](https://github.com/makermusings/fauxmo).

## How it works

```
+----------+   SSDP/UDP 1900    +-------------------------+
|          | -----------------> |  SSDP Responder          |
|  Alexa   | <----------------- |  (upnp.py)              |
|  Echo    |  one response per  |  responds with one entry |
|          |  WeMo device       |  per entity              |
|          |                    +------------+------------+
|          |  GET /setup.xml                 |  per-device
|          | --------------------------------+  TCP ports
|          |  POST /upnp/control/basicevent1 |  (50000+)
|          | <-------------------------------+
+----------+   SOAP responses   +------------+------------+
                                |  WeMo Device Servers     |
                                |  (wemo_api.py)           |
                                |  one aiohttp server per  |
                                |  exposed entity          |
                                +------------+------------+
                                             |
                                  calls HA services
                                  (turn_on / turn_off)
                                             |
                                             v
                                +-------------------------+
                                |   Home Assistant        |
                                |   entity states         |
                                +-------------------------+
```

1. **SSDP discovery** — A UDP responder on port 1900 answers Alexa's
   M-SEARCH broadcasts. Each registered entity gets its own SSDP
   response advertising its individual HTTP server location.
2. **Per-entity WeMo servers** — Each exposed entity runs its own
   aiohttp HTTP server on a dedicated port (base port + deterministic
   offset). Alexa fetches `GET /setup.xml` and sends SOAP commands to
   `POST /upnp/control/basicevent1`.
3. **Entity control** — On/off SOAP commands are translated to
   `homeassistant.turn_on` / `homeassistant.turn_off` service calls
   (scenes use `scene.turn_on`; scene turn_off is a no-op).

## Why WeMo instead of Hue?

The Philips Hue emulation approach requires the HTTP server to be
reachable on **port 80**, which conflicts with other services (e.g.
NGINX, another web server). The Belkin WeMo approach gives each entity
its own port in the 50000+ range — no port 80, no reverse proxy
needed.

## Supported entity domains

`input_boolean`, `light`, `scene`, `script`, `switch`

## Maximum entities

FauxMo supports up to **255** exposed entities per installation.

## Installation

### HACS (recommended)

1. Open **HACS → Integrations → ⋮ (three dots) → Custom repositories**.
2. Add this repository URL and select category **Integration**.
3. Search for **FauxMo** and click **Download**.
4. Restart Home Assistant.

### Manual

Copy `custom_components/fauxmo/` into your Home Assistant
`config/custom_components/` directory. Restart Home Assistant.

## Configuration

After installation, add the integration via the UI:

**Settings → Devices & Services → Add Integration → FauxMo**

| Option     | Default | Description |
|------------|---------|-------------|
| Base port  | 50000   | Starting TCP port. Each entity is assigned a port in the range `[base_port, base_port + 255)`. |

### Selecting entities

Open the integration's **Options** to pick which entities to expose
and set custom Alexa-visible names:

1. Select entities from the supported domains (max 255).
2. Set a custom name for each (or leave blank to use the
   entity's friendly name).
3. Save. Changes apply immediately — no restart required.

### Migration from Emulated Hue

If you have an existing **Emulated Hue** config entry, FauxMo will
detect it on first setup and offer to import your entities automatically.

## Architecture

```
custom_components/fauxmo/
├── __init__.py        # Integration lifecycle: setup, unload, options update
├── config_flow.py     # Config flow UI (setup + options + migration)
├── const.py           # Constants (ports, domain, WeMo protocol values)
├── diagnostics.py     # Diagnostic data export
├── manifest.json      # HA integration manifest
├── quality_scale.yaml # HA quality scale checklist
├── store.py           # Persistent per-entity activity tracking
├── strings.json       # Localisation source strings
├── translations/
│   └── en.json        # English translations
├── upnp.py            # SSDP/UPnP discovery responder
└── wemo_api.py        # WeMo device servers (one per entity)

tests/components/fauxmo/
├── conftest.py         # Shared fixtures
├── test_config_flow.py
├── test_wemo_api.py
├── test_init.py
├── test_store.py
└── test_upnp.py
```

### Key design decisions

- **Per-entity ports** — Each entity gets a deterministic port
  (`base_port + sum(ord(c) for c in entity_id) % 255`), with
  collision resolution. No port 80 dependency.
- **Deterministic serials** — Device serial numbers are derived from
  entity names (matching the original fauxmo algorithm) so they remain
  stable across restarts.
- **No polling** — State is read from HA at SOAP request time; SSDP
  uses UDP multicast. Classification: `local_push`.
- **Debounce** — A 300 ms debounce window prevents duplicate commands
  from multiple Alexa devices responding simultaneously.
- **Activity tracking** — `store.py` records per-entity
  `first_discovered` and `last_controlled` timestamps, viewable in
  the diagnostics panel.
- **Auto-reload on port change** — Changing the base port in the
  options flow triggers a full integration reload.

## Known issues

### Home Assistant discovers "Belkin WeMo" devices

Because FauxMo emulates Belkin WeMo switches on your network, Home
Assistant's built-in **WeMo** integration will detect them and show a
discovery notification asking you to set up "Belkin WeMo". **Ignore
and dismiss this notification** — these are your own emulated devices,
not real WeMo hardware. You can click **Ignore** on the notification
to prevent it from appearing again.

## Alexa discovery checklist

If Alexa doesn't find your devices, verify:

1. **SSDP reachable** — Check HA logs for M-SEARCH activity from
   Alexa's IP.
2. **Device servers running** — In the diagnostics panel, confirm
   `device_servers_running` matches your entity count.
3. **setup.xml reachable** — `curl http://<HA_IP>:<device_port>/setup.xml`
   should return Belkin device XML.
4. **Same subnet** — Alexa and HA must be on the same LAN subnet
   (SSDP multicast doesn't cross routers).
5. **Firewall** — Ensure UDP 1900 and the device ports (default
   50000–50254) are not blocked between Alexa and HA.

## Development

### Running tests

```sh
pytest tests/components/fauxmo/ -v
```

### Logging

```yaml
# configuration.yaml
logger:
  logs:
    custom_components.fauxmo: debug
```

## Credits

Protocol implementation inspired by
[fauxmo](https://github.com/n8henrie/fauxmo) by Nathan Henrie,
originally forked from
[makermusings/fauxmo](https://github.com/makermusings/fauxmo).

## License

This project is provided under the same license as Home Assistant Core.

