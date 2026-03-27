"""Tests for the SSDP/UPnP responder."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.fauxmo.upnp import SSDPDevice, UPnPResponder

from .conftest import MOCK_HOST_IP

MOCK_PORT = 51000
MOCK_SERIAL = "abc123def456"


def _make_device(
    name: str = "Test Device",
    serial: str = MOCK_SERIAL,
    host_ip: str = MOCK_HOST_IP,
    port: int = MOCK_PORT,
) -> SSDPDevice:
    return SSDPDevice(name=name, serial=serial, host_ip=host_ip, port=port)


def _create_responder(devices: list[SSDPDevice] | None = None) -> UPnPResponder:
    """Create a test UPnP responder with optional devices registered."""
    responder = UPnPResponder(host_ip=MOCK_HOST_IP)
    if devices is not None:
        responder.set_devices(devices)
    return responder


# ---------------------------------------------------------------------------
# SSDPDevice property tests
# ---------------------------------------------------------------------------


def test_ssdp_device_location() -> None:
    """SSDPDevice.location should point to setup.xml on the device port."""
    device = _make_device()
    assert device.location == f"http://{MOCK_HOST_IP}:{MOCK_PORT}/setup.xml"


def test_ssdp_device_usn_contains_serial() -> None:
    """SSDPDevice.usn should contain the serial."""
    device = _make_device(serial="myserial123")
    assert "myserial123" in device.usn


def test_ssdp_device_persistent_uuid_contains_serial() -> None:
    """SSDPDevice.persistent_uuid should contain the serial."""
    device = _make_device(serial="myserial123")
    assert "myserial123" in device.persistent_uuid


# ---------------------------------------------------------------------------
# UPnPResponder response format tests
# ---------------------------------------------------------------------------


def test_search_response_contains_location() -> None:
    """The M-SEARCH response for a device should include its LOCATION."""
    device = _make_device()
    response = UPnPResponder._build_search_response(device)

    assert "HTTP/1.1 200 OK" in response
    assert f"http://{MOCK_HOST_IP}:{MOCK_PORT}/setup.xml" in response
    assert "CACHE-CONTROL:" in response
    assert "USN:" in response
    assert response.endswith("\r\n\r\n")


def test_search_response_contains_search_target() -> None:
    """The M-SEARCH response should contain the WeMo search target."""
    from custom_components.fauxmo.const import SSDP_SEARCH_TARGET

    device = _make_device()
    response = UPnPResponder._build_search_response(device)
    assert SSDP_SEARCH_TARGET in response


# ---------------------------------------------------------------------------
# UPnPResponder datagram handling tests
# ---------------------------------------------------------------------------


def test_datagram_ignores_non_msearch() -> None:
    """Non-M-SEARCH messages should be silently ignored."""
    responder = _create_responder([_make_device()])
    mock_transport = MagicMock()
    responder._transport = mock_transport

    responder.datagram_received(
        b"NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n\r\n",
        ("198.51.100.10", 1234),
    )
    mock_transport.sendto.assert_not_called()


def test_datagram_responds_to_belkin_msearch() -> None:
    """M-SEARCH for urn:Belkin:device:** should get one response per device."""
    device = _make_device()
    responder = _create_responder([device])
    mock_transport = MagicMock()
    responder._transport = mock_transport

    msearch = (
        b"M-SEARCH * HTTP/1.1\r\n"
        b"HOST: 239.255.255.250:1900\r\n"
        b'MAN: "ssdp:discover"\r\n'
        b"ST: urn:Belkin:device:**\r\n"
        b"MX: 3\r\n"
        b"\r\n"
    )
    responder.datagram_received(msearch, ("198.51.100.10", 1234))
    assert mock_transport.sendto.call_count == 1
    sent = mock_transport.sendto.call_args[0][0]
    assert b"HTTP/1.1 200 OK" in sent
    assert b"setup.xml" in sent


def test_datagram_responds_to_upnp_rootdevice() -> None:
    """M-SEARCH for upnp:rootdevice should also trigger a response."""
    responder = _create_responder([_make_device()])
    mock_transport = MagicMock()
    responder._transport = mock_transport

    msearch = (
        b"M-SEARCH * HTTP/1.1\r\n"
        b"HOST: 239.255.255.250:1900\r\n"
        b"ST: upnp:rootdevice\r\n"
        b"\r\n"
    )
    responder.datagram_received(msearch, ("198.51.100.10", 1234))
    assert mock_transport.sendto.call_count == 1


def test_datagram_responds_to_ssdp_all() -> None:
    """M-SEARCH for ssdp:all should get one response per device."""
    responder = _create_responder([_make_device(), _make_device(name="Device 2", serial="aaa", port=51001)])
    mock_transport = MagicMock()
    responder._transport = mock_transport

    msearch = (
        b"M-SEARCH * HTTP/1.1\r\n"
        b"HOST: 239.255.255.250:1900\r\n"
        b"ST: ssdp:all\r\n"
        b"\r\n"
    )
    responder.datagram_received(msearch, ("198.51.100.10", 1234))
    assert mock_transport.sendto.call_count == 2


def test_datagram_ignores_unrelated_search() -> None:
    """M-SEARCH for unrelated device types should be ignored."""
    responder = _create_responder([_make_device()])
    mock_transport = MagicMock()
    responder._transport = mock_transport

    msearch = (
        b"M-SEARCH * HTTP/1.1\r\n"
        b"HOST: 239.255.255.250:1900\r\n"
        b"ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
        b"\r\n"
    )
    responder.datagram_received(msearch, ("198.51.100.10", 1234))
    mock_transport.sendto.assert_not_called()


def test_no_transport_does_not_raise() -> None:
    """Datagram handling without a transport should not raise."""
    responder = _create_responder([_make_device()])
    # _transport is None (never connected)

    msearch = (
        b"M-SEARCH * HTTP/1.1\r\n"
        b"ST: ssdp:all\r\n"
        b"\r\n"
    )
    # Should not raise
    responder.datagram_received(msearch, ("198.51.100.10", 1234))


def test_set_devices_updates_list() -> None:
    """set_devices should replace the registered device list."""
    responder = _create_responder()
    assert responder._devices == []

    devices = [_make_device(), _make_device(name="B", serial="bbb", port=51001)]
    responder.set_devices(devices)
    assert len(responder._devices) == 2

    responder.set_devices([])
    assert responder._devices == []

