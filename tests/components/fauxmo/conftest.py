"""Shared test fixtures for the FauxMo integration tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fauxmo.const import (
    CONF_BASE_PORT,
    CONF_ENTITIES,
    CONF_ENTITY_NAME,
    DEFAULT_BASE_PORT,
    DOMAIN,
)

MOCK_ENTITIES: dict[str, dict[str, str]] = {
    "input_boolean.test_switch": {CONF_ENTITY_NAME: "Test Switch"},
    "light.living_room": {CONF_ENTITY_NAME: "Living Room"},
    "scene.movie_time": {CONF_ENTITY_NAME: "Movie Time"},
}

MOCK_HOST_IP = "192.168.1.100"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the custom integration in every test."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry for FauxMo."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="FauxMo",
        data={CONF_BASE_PORT: DEFAULT_BASE_PORT},
        options={CONF_ENTITIES: MOCK_ENTITIES},
        unique_id=DOMAIN,
    )


@pytest.fixture
def mock_empty_config_entry() -> MockConfigEntry:
    """Create a config entry with no entities configured."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="FauxMo",
        data={CONF_BASE_PORT: DEFAULT_BASE_PORT},
        options={CONF_ENTITIES: {}},
        unique_id=DOMAIN,
    )


@pytest.fixture
def patch_host_ip() -> Generator[None]:
    """Patch the source IP lookup to return a deterministic IP."""
    with patch(
        "custom_components.fauxmo.async_get_source_ip",
        AsyncMock(return_value=MOCK_HOST_IP),
    ):
        yield


@pytest.fixture
def patch_upnp() -> Generator[None]:
    """Patch SSDP responder creation to avoid real UDP sockets in tests."""
    mock_transport = AsyncMock()
    mock_transport.close = lambda: None

    async def mock_create(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
        return mock_transport, AsyncMock()

    with patch(
        "custom_components.fauxmo.create_upnp_responder",
        side_effect=mock_create,
    ):
        yield
