# FauxMo — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)

**Talk to Home Assistant from Alexa — locally, with no cloud.**

FauxMo lets an Amazon Echo control Home Assistant directly over your
local network. There's no Nabu Casa subscription, no Alexa skill, no
account linking and nothing to expose to the internet.

Pick the entities you want Alexa to see (lights, switches, input
booleans, scenes and scripts) and FauxMo presents each one to Alexa as
a Belkin WeMo smart plug. Because scripts are supported, any custom
action you build in Home Assistant can be triggered by voice: create a
script called "Movie Night" and say *"Alexa, turn on Movie Night"*. You
can also add these devices to Alexa routines.

FauxMo is a friendlier replacement for Home Assistant's built-in
**Emulated Hue**:

- **Set up entirely in the UI.** No YAML, and changes apply without a
  restart.
- **Works on any port.** Emulated Hue needs port 80, which often clashes
  with NGINX or other add-ons. FauxMo uses a configurable port range
  (50000+ by default).
- **Custom names per entity.** Choose exactly what Alexa calls each
  device.

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

Alexa only talks to an emulated Philips Hue bridge on **port 80**,
which conflicts with other services (e.g. NGINX, another web server).
Alexa reaches emulated Belkin WeMo devices on whatever port they
advertise, so FauxMo gives each entity its own port in a configurable
range: no port 80 and no reverse proxy needed.

## Supported entity domains

`input_boolean`, `light`, `scene`, `script`, `switch`

## Maximum entities

FauxMo supports up to **255** exposed entities per installation.

## Installation

### Home Assistant Marketplace (recommended)

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=MichaelB2018&repository=fauxmo&category=integration)

On Home Assistant 2026.11 or later:

1. Open **Marketplace** in Home Assistant and search for **FauxMo**, or
   click the button above.
2. Click **Download**, then restart Home Assistant.

### HACS (Home Assistant before 2026.11)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=MichaelB2018&repository=fauxmo&category=integration)

1. Open **HACS** and search for **FauxMo**, or click the button above.
2. Click **Download**, then restart Home Assistant.

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

## Architecture

```
custom_components/fauxmo/
├── __init__.py        # Integration lifecycle: setup, unload, options update
├── config_flow.py     # Config flow UI (setup + options)
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

This project is released under the [MIT License](LICENSE). It includes
code derived from [fauxmo](https://github.com/n8henrie/fauxmo)
(original work copyright (c) 2015 Maker Musings, modified work
copyright 2015 Nathan Henrie), also under the MIT License.

