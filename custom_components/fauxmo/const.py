"""Constants for the FauxMo integration."""

from typing import Final

DOMAIN: Final = "fauxmo"

DEFAULT_BASE_PORT: Final = 50000
MAX_ENTITIES: Final = 255

# Config entry keys
CONF_BASE_PORT: Final = "base_port"
CONF_ENTITIES: Final = "entities"
CONF_ENTITY_NAME: Final = "name"

# WeMo / Belkin constants
WEMO_MANUFACTURER: Final = "Belkin International Inc."
WEMO_MODEL_NAME: Final = "Socket"
WEMO_MODEL_NUMBER: Final = "3.1415"
WEMO_DEVICE_TYPE: Final = "urn:Belkin:device:controllee:1"
WEMO_SERVICE_TYPE: Final = "urn:Belkin:service:basicevent:1"
WEMO_SERVER_VERSION: Final = "Unspecified, UPnP/1.0, Unspecified"

# SSDP constants
SSDP_MULTICAST_ADDR: Final = "239.255.255.250"
SSDP_PORT: Final = 1900
SSDP_MAX_AGE: Final = 86400
SSDP_SEARCH_TARGET: Final = "urn:Belkin:device:**"

# Debounce for multi-Echo environments
DEBOUNCE_SECONDS: Final = 0.3

# Entity domain filter for the entity picker
SUPPORTED_DOMAINS: Final = frozenset(
    {"input_boolean", "scene", "switch", "light", "script"}
)
