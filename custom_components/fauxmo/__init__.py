"""The FauxMo integration.

Exposes Home Assistant entities as Belkin WeMo switches on the local
network, allowing Alexa to discover and control them without requiring
port 80 or a reverse proxy.  Each entity gets its own TCP port.

Based on the fauxmo project by Nathan Henrie:
https://github.com/n8henrie/fauxmo
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.network import get_url
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BASE_PORT,
    CONF_ENTITIES,
    DEFAULT_BASE_PORT,
    DOMAIN,
)
from .store import STORAGE_KEY, STORAGE_VERSION, ActivityTracker
from .upnp import SSDPDevice, create_upnp_responder
from .wemo_api import WeMoDeviceManager

_LOGGER = logging.getLogger(__name__)

type FauxMoConfigEntry = ConfigEntry


# ------------------------------------------------------------------
# Config-entry lifecycle
# ------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FauxMoConfigEntry,
) -> bool:
    """Set up FauxMo from a config entry."""
    base_port: int = entry.data.get(CONF_BASE_PORT, DEFAULT_BASE_PORT)
    entities: dict[str, dict[str, str]] = entry.options.get(CONF_ENTITIES, {})

    host_ip = _get_host_ip(hass)

    # Activity tracking (persistent per-entity timestamps)
    tracker = ActivityTracker(
        Store(hass, STORAGE_VERSION, STORAGE_KEY)
    )
    await tracker.async_load()

    _LOGGER.info(
        "Starting FauxMo on %s (base_port=%s, %d entities)",
        host_ip,
        base_port,
        len(entities),
    )

    # Start per-entity WeMo device servers
    device_manager = WeMoDeviceManager(
        hass=hass,
        host_ip=host_ip,
        base_port=base_port,
        activity_tracker=tracker,
    )
    await device_manager.update_entities(entities)

    # Start SSDP responder
    ssdp_transport: asyncio.DatagramTransport | None = None
    ssdp_protocol: Any = None
    try:
        ssdp_transport, ssdp_protocol = await create_upnp_responder(
            hass, host_ip
        )
        # Register current devices with SSDP
        _update_ssdp_devices(ssdp_protocol, device_manager, host_ip)
    except OSError as err:
        _LOGGER.warning(
            "Failed to start SSDP responder (Alexa discovery may not work): %s",
            err,
        )

    # Store references for cleanup and options updates
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "device_manager": device_manager,
        "ssdp_transport": ssdp_transport,
        "ssdp_protocol": ssdp_protocol,
        "activity_tracker": tracker,
        "base_port": base_port,
    }

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async def _async_on_stop(event: Event) -> None:
        await _async_cleanup(hass, entry)

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_on_stop)
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: FauxMoConfigEntry,
) -> bool:
    """Unload a FauxMo config entry."""
    await _async_cleanup(hass, entry)
    return True


async def _async_cleanup(
    hass: HomeAssistant,
    entry: FauxMoConfigEntry,
) -> None:
    """Stop all WeMo device servers and the SSDP responder."""
    data: dict[str, Any] | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if data is None:
        return

    device_manager: WeMoDeviceManager | None = data.get("device_manager")
    ssdp_transport: asyncio.DatagramTransport | None = data.get("ssdp_transport")
    tracker: ActivityTracker | None = data.get("activity_tracker")

    if tracker is not None:
        await tracker.async_save()

    if device_manager is not None:
        await device_manager.stop_all()

    if ssdp_transport is not None:
        ssdp_transport.close()

    _LOGGER.info("FauxMo stopped")


async def _async_options_updated(
    hass: HomeAssistant,
    entry: FauxMoConfigEntry,
) -> None:
    """Handle options update — refresh entities or full reload if port changed."""
    data: dict[str, Any] | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if data is None:
        return

    # If base_port changed, a full reload is needed
    old_port = data.get("base_port")
    new_port = entry.data.get(CONF_BASE_PORT, DEFAULT_BASE_PORT)

    if old_port != new_port:
        _LOGGER.info("Port configuration changed — reloading FauxMo")
        await hass.config_entries.async_reload(entry.entry_id)
        return

    device_manager: WeMoDeviceManager = data["device_manager"]
    entities: dict[str, dict[str, str]] = entry.options.get(CONF_ENTITIES, {})
    await device_manager.update_entities(entities)

    # Update SSDP device list
    ssdp_protocol = data.get("ssdp_protocol")
    if ssdp_protocol is not None:
        _update_ssdp_devices(ssdp_protocol, device_manager, device_manager.host_ip)

    _LOGGER.info(
        "FauxMo entity mapping updated (%d entities)", len(entities)
    )


def _update_ssdp_devices(
    ssdp_protocol: Any,
    device_manager: WeMoDeviceManager,
    host_ip: str,
) -> None:
    """Push device list to the SSDP responder."""
    ssdp_devices = [
        SSDPDevice(
            name=info.name,
            serial=info.serial,
            host_ip=host_ip,
            port=info.port,
        )
        for info in device_manager.devices.values()
    ]
    ssdp_protocol.set_devices(ssdp_devices)


def _get_host_ip(hass: HomeAssistant) -> str:
    """Determine the host IP address to advertise."""
    try:
        url = get_url(hass, allow_external=False, prefer_external=False)
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname
        if host and host not in ("localhost", "127.0.0.1", "::1"):
            return host
    except Exception:
        pass

    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
