"""Config flow for Laifen integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Laifen."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if not bluetooth.async_scanner_count(self.hass, connectable=False):
            return self.async_abort(reason="bluetooth_not_available")

        # Automatically search for a Bluetooth device with a name starting with "LFTB"
        devices = bluetooth.async_discovered_service_info(self.hass)
        for device in devices:
            if device.name.startswith("LFTB"):
                address = device.address
                return self.async_create_entry(title="Laifen Toothbrush", data={"mac": address})

        return self.async_abort(reason="no_matching_device_found")
