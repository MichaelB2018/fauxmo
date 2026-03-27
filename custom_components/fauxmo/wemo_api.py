"""Belkin WeMo device emulation for the FauxMo integration.

Each exposed Home Assistant entity is presented as a separate Belkin WeMo
switch on the network, with its own TCP port and SSDP identity.  Alexa
discovers and controls them via the standard UPnP / SOAP protocol.

Protocol implementation inspired by:
- fauxmo by Nathan Henrie (https://github.com/n8henrie/fauxmo)
  Originally forked from https://github.com/makermusings/fauxmo
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import web

from homeassistant.const import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
)
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ENTITY_NAME,
    DEBOUNCE_SECONDS,
    DEFAULT_BASE_PORT,
    MAX_ENTITIES,
    WEMO_DEVICE_TYPE,
    WEMO_MANUFACTURER,
    WEMO_MODEL_NAME,
    WEMO_MODEL_NUMBER,
    WEMO_SERVER_VERSION,
    WEMO_SERVICE_TYPE,
)
from .store import ActivityTracker

_LOGGER = logging.getLogger(__name__)

# Belkin device description XML template (setup.xml)
SETUP_XML = """\
<?xml version="1.0"?>
<root>
 <device>
    <deviceType>{device_type}</deviceType>
    <friendlyName>{device_name}</friendlyName>
    <manufacturer>{manufacturer}</manufacturer>
    <modelName>{model_name}</modelName>
    <modelNumber>{model_number}</modelNumber>
    <modelDescription>Belkin Plugin Socket 1.0</modelDescription>
    <UDN>uuid:Socket-1_0-{device_serial}</UDN>
    <serialNumber>{device_serial}</serialNumber>
    <binaryState>0</binaryState>
    <serviceList>
      <service>
          <serviceType>{service_type}</serviceType>
          <serviceId>urn:Belkin:serviceId:basicevent1</serviceId>
          <controlURL>/upnp/control/basicevent1</controlURL>
          <eventSubURL>/upnp/event/basicevent1</eventSubURL>
          <SCPDURL>/eventservice.xml</SCPDURL>
      </service>
    </serviceList>
 </device>
</root>"""

# SOAP envelope for GetBinaryState response
GET_STATE_RESPONSE = """\
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" \
s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:GetBinaryStateResponse xmlns:u="urn:Belkin:service:basicevent:1">
<BinaryState>{state}</BinaryState>
</u:GetBinaryStateResponse>
</s:Body>
</s:Envelope>"""


def make_serial(name: str) -> str:
    """Generate a deterministic serial/UUID from a device name.

    Matches the algorithm from the original fauxmo implementation.
    """
    raw = "%sfauxmo!" % name
    parts = ["%x" % sum(ord(c) for c in name)]
    parts.extend("%x" % ord(c) for c in raw)
    return "".join(parts)[:14]


def assign_port(entity_id: str, base_port: int) -> int:
    """Compute a deterministic port for an entity ID.

    Uses a stable hash of the entity_id string (sum of char codes)
    to produce a port in the range [base_port, base_port + MAX_ENTITIES).
    """
    char_sum = sum(ord(c) for c in entity_id)
    return base_port + (char_sum % MAX_ENTITIES)


class DeviceInfo:
    """Lightweight descriptor for a registered WeMo device."""

    __slots__ = ("entity_id", "name", "serial", "port")

    def __init__(self, entity_id: str, name: str, serial: str, port: int) -> None:
        self.entity_id = entity_id
        self.name = name
        self.serial = serial
        self.port = port


class WeMoDeviceServer:
    """A mini HTTP server that emulates a single Belkin WeMo switch."""

    def __init__(
        self,
        hass: HomeAssistant,
        info: DeviceInfo,
        activity_tracker: ActivityTracker | None = None,
    ) -> None:
        self.hass = hass
        self.info = info
        self.activity_tracker = activity_tracker
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._last_command_time: float = 0.0

    async def start(self) -> None:
        """Start the aiohttp server for this device."""
        app = web.Application()
        app.router.add_get("/setup.xml", self._handle_setup_xml)
        app.router.add_post(
            "/upnp/control/basicevent1", self._handle_basicevent
        )
        # Some Alexa firmware also does GET on the control URL
        app.router.add_get(
            "/upnp/control/basicevent1", self._handle_basicevent
        )

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.info.port)
        await self._site.start()
        _LOGGER.debug(
            "WeMo device '%s' listening on port %s (serial %s)",
            self.info.name,
            self.info.port,
            self.info.serial,
        )

    async def stop(self) -> None:
        """Stop and clean up the server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    @property
    def running(self) -> bool:
        """Return True if the server is active."""
        return self._runner is not None

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def _handle_setup_xml(self, request: web.Request) -> web.Response:
        """Serve the Belkin device description XML."""
        if self.activity_tracker is not None:
            self.activity_tracker.record_discovery(self.info.entity_id)

        xml = SETUP_XML.format(
            device_type=WEMO_DEVICE_TYPE,
            device_name=self.info.name,
            manufacturer=WEMO_MANUFACTURER,
            model_name=WEMO_MODEL_NAME,
            model_number=WEMO_MODEL_NUMBER,
            service_type=WEMO_SERVICE_TYPE,
            device_serial=self.info.serial,
        )
        return web.Response(text=xml, content_type="text/xml")

    async def _handle_basicevent(
        self, request: web.Request
    ) -> web.Response:
        """Handle SOAP SetBinaryState / GetBinaryState requests."""
        try:
            body = (await request.read()).decode("utf-8", errors="replace")
        except Exception:
            body = ""

        # Check for SetBinaryState
        if "SetBinaryState" in body:
            return await self._handle_set_binary_state(body)

        # Check for GetBinaryState
        if "GetBinaryState" in body:
            return self._handle_get_binary_state()

        # Unknown SOAP action
        _LOGGER.debug(
            "WeMo '%s': unrecognised SOAP request:\n%s",
            self.info.name,
            body[:500],
        )
        return web.Response(status=400)

    async def _handle_set_binary_state(self, body: str) -> web.Response:
        """Process a SetBinaryState SOAP command (on/off)."""
        # Debounce: ignore rapid duplicate commands from multiple Echos
        now = time.monotonic()
        if (now - self._last_command_time) < DEBOUNCE_SECONDS:
            _LOGGER.debug(
                "WeMo '%s': debounced duplicate command", self.info.name
            )
            return self._soap_success_response()
        self._last_command_time = now

        entity_id = self.info.entity_id
        domain = entity_id.split(".")[0]

        if "<BinaryState>1</BinaryState>" in body:
            is_on = True
        elif "<BinaryState>0</BinaryState>" in body:
            is_on = False
        else:
            _LOGGER.warning(
                "WeMo '%s': SetBinaryState with unknown value", self.info.name
            )
            return self._soap_success_response()

        service = SERVICE_TURN_ON if is_on else SERVICE_TURN_OFF

        # Scenes only support turn_on
        if domain == "scene":
            if is_on:
                await self.hass.services.async_call(
                    "scene", SERVICE_TURN_ON, {"entity_id": entity_id}
                )
        else:
            await self.hass.services.async_call(
                "homeassistant", service, {"entity_id": entity_id}
            )

        if self.activity_tracker is not None:
            self.activity_tracker.record_control(entity_id)
            self.hass.async_create_task(self.activity_tracker.async_save())

        _LOGGER.info(
            "WeMo '%s' (%s): %s",
            self.info.name,
            entity_id,
            "ON" if is_on else "OFF",
        )
        return self._soap_success_response()

    def _handle_get_binary_state(self) -> web.Response:
        """Return the current binary state of the entity."""
        state_obj = self.hass.states.get(self.info.entity_id)
        state_val = 1 if (state_obj is not None and state_obj.state == STATE_ON) else 0

        soap = GET_STATE_RESPONSE.format(state=state_val)
        return web.Response(
            text=soap,
            content_type="text/xml",
            charset="utf-8",
        )

    @staticmethod
    def _soap_success_response() -> web.Response:
        """Return an empty 200 OK (Echo doesn't validate the SOAP body)."""
        return web.Response(
            text="",
            content_type="text/xml",
            charset="utf-8",
        )


class WeMoDeviceManager:
    """Manages the lifecycle of all WeMo device servers."""

    def __init__(
        self,
        hass: HomeAssistant,
        host_ip: str,
        base_port: int = DEFAULT_BASE_PORT,
        activity_tracker: ActivityTracker | None = None,
    ) -> None:
        self.hass = hass
        self.host_ip = host_ip
        self.base_port = base_port
        self.activity_tracker = activity_tracker
        self._devices: dict[str, WeMoDeviceServer] = {}
        self._device_info: dict[str, DeviceInfo] = {}
        self._used_ports: set[int] = set()

    @property
    def devices(self) -> dict[str, DeviceInfo]:
        """Return a read-only view of currently registered devices."""
        return dict(self._device_info)

    def get_device_ports(self) -> dict[str, int]:
        """Return entity_id → port mapping for all active devices."""
        return {eid: info.port for eid, info in self._device_info.items()}

    async def start_device(
        self, entity_id: str, name: str
    ) -> DeviceInfo:
        """Create and start a WeMo device server for one entity."""
        if entity_id in self._devices:
            return self._device_info[entity_id]

        if len(self._devices) >= MAX_ENTITIES:
            raise ValueError(
                f"Maximum number of FauxMo devices ({MAX_ENTITIES}) reached"
            )

        serial = make_serial(name)
        port = self._allocate_port(entity_id)
        info = DeviceInfo(entity_id, name, serial, port)

        server = WeMoDeviceServer(
            self.hass, info, self.activity_tracker
        )
        try:
            await server.start()
        except OSError as err:
            _LOGGER.error(
                "Failed to start WeMo device '%s' on port %s: %s",
                name,
                port,
                err,
            )
            self._used_ports.discard(port)
            raise

        self._devices[entity_id] = server
        self._device_info[entity_id] = info

        _LOGGER.info(
            "FauxMo device '%s' ready on %s:%s",
            name,
            self.host_ip,
            port,
        )
        return info

    async def stop_device(self, entity_id: str) -> None:
        """Stop and remove a device server."""
        server = self._devices.pop(entity_id, None)
        info = self._device_info.pop(entity_id, None)
        if server is not None:
            await server.stop()
        if info is not None:
            self._used_ports.discard(info.port)

    async def stop_all(self) -> None:
        """Stop all device servers."""
        for entity_id in list(self._devices.keys()):
            await self.stop_device(entity_id)

    async def update_entities(
        self, entities: dict[str, dict[str, str]]
    ) -> None:
        """Synchronise running devices to match the given entity config.

        Starts new devices, stops removed ones. Existing devices whose
        name changed are restarted.
        """
        current_ids = set(self._devices.keys())
        desired_ids = set(entities.keys())

        # Stop removed entities
        for entity_id in current_ids - desired_ids:
            _LOGGER.info("Removing FauxMo device for %s", entity_id)
            await self.stop_device(entity_id)

        # Start new entities
        for entity_id in desired_ids - current_ids:
            name = self._resolve_name(entity_id, entities[entity_id])
            await self.start_device(entity_id, name)

        # Check for name changes on existing entities
        for entity_id in current_ids & desired_ids:
            new_name = self._resolve_name(entity_id, entities[entity_id])
            old_info = self._device_info.get(entity_id)
            if old_info and old_info.name != new_name:
                _LOGGER.info(
                    "FauxMo device %s renamed '%s' → '%s', restarting",
                    entity_id,
                    old_info.name,
                    new_name,
                )
                await self.stop_device(entity_id)
                await self.start_device(entity_id, new_name)

    def _resolve_name(
        self, entity_id: str, entity_conf: dict[str, str]
    ) -> str:
        """Determine the display name for an entity."""
        name = entity_conf.get(CONF_ENTITY_NAME, "")
        if name:
            return name
        state_obj = self.hass.states.get(entity_id)
        if state_obj:
            return state_obj.attributes.get("friendly_name", entity_id)
        return entity_id

    def _allocate_port(self, entity_id: str) -> int:
        """Allocate a unique port for an entity, handling collisions."""
        port = assign_port(entity_id, self.base_port)
        attempts = 0
        while port in self._used_ports:
            attempts += 1
            if attempts > MAX_ENTITIES:
                raise RuntimeError("Cannot allocate port — all slots exhausted")
            port = self.base_port + ((port - self.base_port + 1) % MAX_ENTITIES)
            _LOGGER.warning(
                "Port collision for %s, trying port %s", entity_id, port
            )
        self._used_ports.add(port)
        return port
