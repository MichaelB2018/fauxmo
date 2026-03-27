"""Tests for the WeMo device server."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from custom_components.fauxmo.const import (
    CONF_ENTITY_NAME,
    DEFAULT_BASE_PORT,
)
from custom_components.fauxmo.wemo_api import (
    DeviceInfo,
    WeMoDeviceManager,
    WeMoDeviceServer,
    assign_port,
    make_serial,
)

from .conftest import MOCK_HOST_IP


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


def test_make_serial_is_deterministic() -> None:
    """make_serial should return the same value for the same input."""
    assert make_serial("Test Switch") == make_serial("Test Switch")


def test_make_serial_differs_for_different_names() -> None:
    """make_serial should produce different serials for different names."""
    assert make_serial("Device A") != make_serial("Device B")


def test_make_serial_length() -> None:
    """make_serial result should be at most 14 characters."""
    assert len(make_serial("Some Long Device Name Here")) <= 14


def test_assign_port_in_range() -> None:
    """assign_port should return a port within [base, base+255)."""
    base = DEFAULT_BASE_PORT
    port = assign_port("light.living_room", base)
    assert base <= port < base + 255


def test_assign_port_deterministic() -> None:
    """Same entity_id always gets the same port offset."""
    assert assign_port("light.test", DEFAULT_BASE_PORT) == assign_port(
        "light.test", DEFAULT_BASE_PORT
    )


# ---------------------------------------------------------------------------
# WeMoDeviceServer HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def wemo_client(hass: HomeAssistant, aiohttp_client) -> TestClient:
    """Create a test aiohttp client for a single WeMo device server."""
    hass.states.async_set(
        "input_boolean.test_switch", STATE_ON, {"friendly_name": "Test Switch"}
    )
    info = DeviceInfo(
        entity_id="input_boolean.test_switch",
        name="Test Switch",
        serial=make_serial("Test Switch"),
        port=51000,
    )
    server = WeMoDeviceServer(hass=hass, info=info, activity_tracker=None)
    # Build the app without starting it on a real port
    from aiohttp import web

    app = web.Application()
    app.router.add_get("/setup.xml", server._handle_setup_xml)
    app.router.add_post("/upnp/control/basicevent1", server._handle_basicevent)
    app.router.add_get("/upnp/control/basicevent1", server._handle_basicevent)
    return await aiohttp_client(app)


@pytest.fixture
async def scene_client(hass: HomeAssistant, aiohttp_client) -> TestClient:
    """Create a test aiohttp client for a scene WeMo device server."""
    hass.states.async_set("scene.test_scene", STATE_OFF, {"friendly_name": "Test Scene"})
    info = DeviceInfo(
        entity_id="scene.test_scene",
        name="Test Scene",
        serial=make_serial("Test Scene"),
        port=51001,
    )
    server = WeMoDeviceServer(hass=hass, info=info, activity_tracker=None)
    from aiohttp import web

    app = web.Application()
    app.router.add_post("/upnp/control/basicevent1", server._handle_basicevent)
    return await aiohttp_client(app)


async def test_setup_xml_returns_xml(wemo_client: TestClient) -> None:
    """GET /setup.xml should return valid Belkin device XML."""
    resp = await wemo_client.get("/setup.xml")
    assert resp.status == 200
    text = await resp.text()
    assert "urn:Belkin:device:**" in text or "ControlPoint" in text or "Belkin" in text
    assert "Test Switch" in text


async def test_setup_xml_content_type(wemo_client: TestClient) -> None:
    """setup.xml should have text/xml content type."""
    resp = await wemo_client.get("/setup.xml")
    assert "xml" in resp.content_type


async def test_set_binary_state_on_calls_service(
    hass: HomeAssistant,
    wemo_client: TestClient,
) -> None:
    """SetBinaryState 1 should call the turn_on service."""
    calls: list[dict[str, Any]] = []

    async def mock_call(
        domain: str, service: str, service_data: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        calls.append({"domain": domain, "service": service, "data": service_data})

    hass.services.async_call = mock_call  # type: ignore[assignment]

    soap_body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body>"
        '<u:SetBinaryState xmlns:u="urn:Belkin:service:basicevent:1">'
        "<BinaryState>1</BinaryState>"
        "</u:SetBinaryState>"
        "</s:Body>"
        "</s:Envelope>"
    )
    resp = await wemo_client.post(
        "/upnp/control/basicevent1",
        data=soap_body.encode(),
        headers={"Content-Type": "text/xml"},
    )
    assert resp.status == 200
    assert any(c["service"] == "turn_on" for c in calls)


async def test_set_binary_state_off_calls_service(
    hass: HomeAssistant,
    wemo_client: TestClient,
) -> None:
    """SetBinaryState 0 should call the turn_off service."""
    calls: list[dict[str, Any]] = []

    async def mock_call(
        domain: str, service: str, service_data: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        calls.append({"domain": domain, "service": service, "data": service_data})

    hass.services.async_call = mock_call  # type: ignore[assignment]

    soap_body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body>"
        '<u:SetBinaryState xmlns:u="urn:Belkin:service:basicevent:1">'
        "<BinaryState>0</BinaryState>"
        "</u:SetBinaryState>"
        "</s:Body>"
        "</s:Envelope>"
    )
    resp = await wemo_client.post(
        "/upnp/control/basicevent1",
        data=soap_body.encode(),
        headers={"Content-Type": "text/xml"},
    )
    assert resp.status == 200
    assert any(c["service"] == "turn_off" for c in calls)


async def test_scene_off_is_noop(
    hass: HomeAssistant,
    scene_client: TestClient,
) -> None:
    """Turning off a scene should be a no-op (scenes are on-only)."""
    calls: list[dict[str, Any]] = []

    async def mock_call(
        domain: str, service: str, service_data: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        calls.append({"domain": domain, "service": service, "data": service_data})

    hass.services.async_call = mock_call  # type: ignore[assignment]

    soap_body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body>"
        '<u:SetBinaryState xmlns:u="urn:Belkin:service:basicevent:1">'
        "<BinaryState>0</BinaryState>"
        "</u:SetBinaryState>"
        "</s:Body>"
        "</s:Envelope>"
    )
    resp = await scene_client.post(
        "/upnp/control/basicevent1",
        data=soap_body.encode(),
        headers={"Content-Type": "text/xml"},
    )
    assert resp.status == 200
    assert len(calls) == 0


async def test_get_binary_state_on(
    hass: HomeAssistant,
    wemo_client: TestClient,
) -> None:
    """GetBinaryState should return 1 when the entity is on."""
    hass.states.async_set("input_boolean.test_switch", STATE_ON)

    soap_body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body>"
        '<u:GetBinaryState xmlns:u="urn:Belkin:service:basicevent:1"/>'
        "</s:Body>"
        "</s:Envelope>"
    )
    resp = await wemo_client.post(
        "/upnp/control/basicevent1",
        data=soap_body.encode(),
        headers={"Content-Type": "text/xml"},
    )
    assert resp.status == 200
    text = await resp.text()
    assert "<BinaryState>1</BinaryState>" in text


async def test_get_binary_state_off(
    hass: HomeAssistant,
    wemo_client: TestClient,
) -> None:
    """GetBinaryState should return 0 when the entity is off."""
    hass.states.async_set("input_boolean.test_switch", STATE_OFF)

    soap_body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body>"
        '<u:GetBinaryState xmlns:u="urn:Belkin:service:basicevent:1"/>'
        "</s:Body>"
        "</s:Envelope>"
    )
    resp = await wemo_client.post(
        "/upnp/control/basicevent1",
        data=soap_body.encode(),
        headers={"Content-Type": "text/xml"},
    )
    assert resp.status == 200
    text = await resp.text()
    assert "<BinaryState>0</BinaryState>" in text


# ---------------------------------------------------------------------------
# WeMoDeviceManager tests
# ---------------------------------------------------------------------------


async def test_manager_starts_and_stops_device(hass: HomeAssistant) -> None:
    """Manager should start a device server and stop it cleanly."""
    manager = WeMoDeviceManager(
        hass=hass,
        host_ip=MOCK_HOST_IP,
        base_port=DEFAULT_BASE_PORT,
        activity_tracker=None,
    )

    with patch(
        "custom_components.fauxmo.wemo_api.web.TCPSite",
    ) as mock_site_cls:
        mock_site = AsyncMock()
        mock_site_cls.return_value = mock_site

        info = await manager.start_device("light.test", "Test Light")

    assert info.entity_id == "light.test"
    assert info.name == "Test Light"
    assert DEFAULT_BASE_PORT <= info.port < DEFAULT_BASE_PORT + 255
    assert "light.test" in manager.devices

    with patch(
        "custom_components.fauxmo.wemo_api.web.TCPSite",
    ):
        await manager.stop_all()

    assert "light.test" not in manager.devices


async def test_manager_max_entities(hass: HomeAssistant) -> None:
    """Manager should reject more than MAX_ENTITIES devices."""
    from custom_components.fauxmo.const import MAX_ENTITIES

    manager = WeMoDeviceManager(
        hass=hass,
        host_ip=MOCK_HOST_IP,
        base_port=DEFAULT_BASE_PORT,
        activity_tracker=None,
    )

    with patch(
        "custom_components.fauxmo.wemo_api.web.TCPSite",
    ) as mock_site_cls:
        mock_site = AsyncMock()
        mock_site_cls.return_value = mock_site

        # Fill to capacity by injecting fake entries directly
        for i in range(MAX_ENTITIES):
            manager._devices[f"light.fake_{i}"] = MagicMock()
            manager._device_info[f"light.fake_{i}"] = DeviceInfo(
                f"light.fake_{i}", f"Fake {i}", f"serial{i}", DEFAULT_BASE_PORT + i
            )
            manager._used_ports.add(DEFAULT_BASE_PORT + i)

        with pytest.raises(ValueError, match="Maximum"):
            await manager.start_device("light.overflow", "Overflow")


async def test_manager_update_entities_adds_and_removes(
    hass: HomeAssistant,
) -> None:
    """update_entities should add new and remove old devices."""
    manager = WeMoDeviceManager(
        hass=hass,
        host_ip=MOCK_HOST_IP,
        base_port=DEFAULT_BASE_PORT,
        activity_tracker=None,
    )

    entities_v1 = {"light.a": {CONF_ENTITY_NAME: "Light A"}}
    entities_v2 = {
        "light.a": {CONF_ENTITY_NAME: "Light A"},
        "light.b": {CONF_ENTITY_NAME: "Light B"},
    }
    entities_v3 = {"light.b": {CONF_ENTITY_NAME: "Light B"}}

    with patch(
        "custom_components.fauxmo.wemo_api.web.TCPSite",
    ) as mock_site_cls:
        mock_site_cls.return_value = AsyncMock()

        await manager.update_entities(entities_v1)
        assert "light.a" in manager.devices
        assert "light.b" not in manager.devices

        await manager.update_entities(entities_v2)
        assert "light.a" in manager.devices
        assert "light.b" in manager.devices

        await manager.update_entities(entities_v3)
        assert "light.a" not in manager.devices
        assert "light.b" in manager.devices

        await manager.stop_all()

