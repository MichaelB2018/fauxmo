"""SSDP/UPnP discovery responder for the FauxMo integration.

Listens for SSDP M-SEARCH requests on UDP multicast 239.255.255.250:1900
and responds with Belkin WeMo device advertisements so that Alexa can
discover each exposed Home Assistant entity as a separate WeMo switch.

Protocol details based on:
- fauxmo by Nathan Henrie (https://github.com/n8henrie/fauxmo)
  Originally forked from https://github.com/makermusings/fauxmo
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import socket
import uuid
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .const import SSDP_MAX_AGE, SSDP_MULTICAST_ADDR, SSDP_PORT, SSDP_SEARCH_TARGET

_LOGGER = logging.getLogger(__name__)


@dataclass
class SSDPDevice:
    """Minimal device descriptor for SSDP responses."""

    name: str
    serial: str
    host_ip: str
    port: int

    @property
    def location(self) -> str:
        """Return the URL to the device setup.xml."""
        return f"http://{self.host_ip}:{self.port}/setup.xml"

    @property
    def usn(self) -> str:
        """Return the Unique Service Name."""
        return f"uuid:Socket-1_0-{self.serial}::{SSDP_SEARCH_TARGET}"

    @property
    def persistent_uuid(self) -> str:
        """Return the persistent UUID for the device."""
        return f"Socket-1_0-{self.serial}"


class UPnPResponder(asyncio.DatagramProtocol):
    """SSDP/UPnP protocol that responds to M-SEARCH requests.

    Responds on behalf of all registered WeMo devices. Each device gets
    its own response with a unique LOCATION pointing to its individual
    setup.xml endpoint.
    """

    def __init__(self, host_ip: str) -> None:
        """Initialise the UPnP responder."""
        self.host_ip = host_ip
        self._transport: asyncio.DatagramTransport | None = None
        self._devices: list[SSDPDevice] = []

    def set_devices(self, devices: list[SSDPDevice]) -> None:
        """Update the list of devices to advertise."""
        self._devices = list(devices)

    # ------------------------------------------------------------------
    # Protocol callbacks
    # ------------------------------------------------------------------

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Handle connection established."""
        self._transport = transport  # type: ignore[assignment]
        _LOGGER.debug(
            "SSDP responder listening on %s:%s", SSDP_MULTICAST_ADDR, SSDP_PORT
        )

    def connection_lost(self, exc: Exception | None) -> None:
        """Handle connection lost."""
        _LOGGER.debug("SSDP responder stopped")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle incoming SSDP M-SEARCH requests."""
        try:
            message = data.decode("utf-8", errors="replace")
        except Exception:
            return

        if "M-SEARCH" not in message:
            return

        message_lower = message.lower()

        # Respond to the search targets that Alexa uses for WeMo discovery
        is_relevant = any(
            target in message_lower
            for target in (
                "urn:belkin:device:**",
                "upnp:rootdevice",
                "ssdp:all",
            )
        )

        if not is_relevant:
            return

        _LOGGER.debug(
            "SSDP M-SEARCH from %s:%s — responding with %d device(s)",
            addr[0],
            addr[1],
            len(self._devices),
        )

        if self._transport is None:
            return

        for device in self._devices:
            response = self._build_search_response(device)
            self._transport.sendto(response.encode("utf-8"), addr)

    # ------------------------------------------------------------------
    # SSDP message builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_search_response(device: SSDPDevice) -> str:
        """Build the SSDP M-SEARCH response for a single device."""
        date_str = email.utils.formatdate(
            timeval=None, localtime=False, usegmt=True
        )
        return (
            "HTTP/1.1 200 OK\r\n"
            f"CACHE-CONTROL: max-age={SSDP_MAX_AGE}\r\n"
            f"DATE: {date_str}\r\n"
            "EXT:\r\n"
            f"LOCATION: {device.location}\r\n"
            'OPT: "http://schemas.upnp.org/upnp/1/0/"; ns=01\r\n'
            f"01-NLS: {uuid.uuid4()}\r\n"
            "SERVER: Unspecified, UPnP/1.0, Unspecified\r\n"
            f"ST: {SSDP_SEARCH_TARGET}\r\n"
            f"USN: uuid:{device.persistent_uuid}::{SSDP_SEARCH_TARGET}\r\n"
            "X-User-Agent: redsonic\r\n"
            "\r\n"
        )


async def create_upnp_responder(
    hass: HomeAssistant,
    host_ip: str,
) -> tuple[asyncio.DatagramTransport, UPnPResponder]:
    """Create and start the SSDP/UPnP responder.

    Returns the transport and protocol so the caller can close/update
    them during integration lifecycle.
    """
    loop = asyncio.get_running_loop()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass  # SO_REUSEPORT not available on Windows

    sock.bind(("", SSDP_PORT))

    group = socket.inet_aton(SSDP_MULTICAST_ADDR)
    mreq = group + socket.inet_aton(host_ip)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setsockopt(
        socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(host_ip)
    )

    sock.setblocking(False)

    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UPnPResponder(host_ip),
        sock=sock,
    )

    return transport, protocol  # type: ignore[return-value]

    _LOGGER.info(
        "SSDP responder started on %s for bridge at %s:%s (advertise_port=%s, LOCATION=%s)",
        SSDP_MULTICAST_ADDR,
        host_ip,
        listen_port,
        advertise_port or listen_port,
        f"http://{host_ip}:{advertise_port or listen_port}/description.xml",
    )

    return transport, protocol  # type: ignore[return-value]
