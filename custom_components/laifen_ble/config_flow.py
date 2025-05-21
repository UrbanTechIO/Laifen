"""Config flow for Laifen integration."""
from __future__ import annotations

import logging
import voluptuous as vol
from typing import Any

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

        # Discover available Bluetooth devices
        devices = bluetooth.async_discovered_service_info(self.hass)

        _LOGGER.warning(f"Discovered devices: {[device.name for device in devices]}")  # Log all discovered devices
        found_devices = {device.name: device.address for device in devices if device.name.startswith("LFTB")}
        
        if not found_devices:
            _LOGGER.error("No Laifen devices found via Bluetooth scan.")
            return self.async_abort(reason="no_matching_device_found")

        # If multiple devices, prompt user to select
        if len(found_devices) > 1:
            return self.async_show_form(
                step_id="select_device",
                data_schema=vol.Schema({
                    vol.Required("mac_address"): vol.In(found_devices.values())
                })
            )

        # Auto-create entry if only one device found
        device_name, device_address = next(iter(found_devices.items()))
        return self.async_create_entry(title="Laifen Toothbrush", data={"devices": [device_address]})

    
    async def async_step_select_device(self, user_input: dict[str, Any]) -> FlowResult:
        """Handle user-selected device registration."""
        selected_address = user_input["mac_address"]
        return self.async_create_entry(title=f"Laifen Toothbrush ({selected_address})", data={"devices": [selected_address]})
