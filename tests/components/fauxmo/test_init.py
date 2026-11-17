"""Tests for the FauxMo integration setup and lifecycle."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.fauxmo.const import (
    CONF_ENTITIES,
    CONF_ENTITY_NAME,
    CONF_BASE_PORT,
    DEFAULT_BASE_PORT,
    DOMAIN,
)

from .conftest import MOCK_ENTITIES, MOCK_HOST_IP


async def test_setup_entry(
    hass: HomeAssistant,
    mock_config_entry,
    patch_host_ip,
    patch_upnp,
) -> None:
    """Test successful setup of a config entry."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.fauxmo.wemo_api.web.TCPSite",
    ) as mock_site_cls:
        mock_site = AsyncMock()
        mock_site_cls.return_value = mock_site

        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert result is True
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


async def test_setup_entry_port_in_use(
    hass: HomeAssistant,
    mock_config_entry,
    patch_host_ip,
    patch_upnp,
) -> None:
    """Setup should retry and stop already-started servers when a port is taken."""
    mock_config_entry.add_to_hass(hass)

    started_sites: list[AsyncMock] = []

    def _make_site(*args: Any, **kwargs: Any) -> AsyncMock:
        site = AsyncMock()
        # Let the first device start, then fail on the second
        if started_sites:
            site.start.side_effect = OSError("Address already in use")
        started_sites.append(site)
        return site

    with (
        patch(
            "custom_components.fauxmo.wemo_api.web.TCPSite",
            side_effect=_make_site,
        ),
        patch(
            "custom_components.fauxmo.wemo_api.web.AppRunner.cleanup",
            autospec=True,
        ) as mock_cleanup,
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert result is False
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})
    # The device that started and the device that failed were both cleaned up
    assert mock_cleanup.call_count == 2


async def test_unload_entry(
    hass: HomeAssistant,
    mock_config_entry,
    patch_host_ip,
    patch_upnp,
) -> None:
    """Test unloading a config entry cleans up resources."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.fauxmo.wemo_api.web.TCPSite",
    ) as mock_site_cls:
        mock_site = AsyncMock()
        mock_site_cls.return_value = mock_site

        await hass.config_entries.async_setup(mock_config_entry.entry_id)

    result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert result is True
    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_options_update_refreshes_entities(
    hass: HomeAssistant,
    mock_config_entry,
    patch_host_ip,
    patch_upnp,
) -> None:
    """Test that updating options refreshes the device manager without restart."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.fauxmo.wemo_api.web.TCPSite",
    ) as mock_site_cls:
        mock_site = AsyncMock()
        mock_site_cls.return_value = mock_site

        await hass.config_entries.async_setup(mock_config_entry.entry_id)

    # Get the running WeMoDeviceManager instance
    device_manager = hass.data[DOMAIN][mock_config_entry.entry_id]["device_manager"]
    assert len(device_manager.devices) == len(MOCK_ENTITIES)

    # Update options with a new entity
    new_entities = {
        **MOCK_ENTITIES,
        "input_boolean.new_device": {CONF_ENTITY_NAME: "New Device"},
    }

    with patch(
        "custom_components.fauxmo.wemo_api.web.TCPSite",
    ) as mock_site_cls:
        mock_site_cls.return_value = AsyncMock()

        hass.config_entries.async_update_entry(
            mock_config_entry,
            options={CONF_ENTITIES: new_entities},
        )
        await hass.async_block_till_done()

    assert len(device_manager.devices) == len(MOCK_ENTITIES) + 1


async def test_ssdp_failure_is_non_fatal(
    hass: HomeAssistant,
    mock_config_entry,
    patch_host_ip,
) -> None:
    """Test that SSDP failure doesn't prevent the integration from loading."""
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.fauxmo.wemo_api.web.TCPSite",
        ) as mock_site_cls,
        patch(
            "custom_components.fauxmo.create_upnp_responder",
            side_effect=OSError("Cannot bind SSDP"),
        ),
    ):
        mock_site = AsyncMock()
        mock_site_cls.return_value = mock_site

        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert result is True
    # SSDP transport should be None
    assert hass.data[DOMAIN][mock_config_entry.entry_id]["ssdp_transport"] is None

