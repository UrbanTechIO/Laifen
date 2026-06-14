"""The Laifen integration models."""

from __future__ import annotations
from dataclasses import dataclass
from .laifen import Laifen


from homeassistant.helpers.update_coordinator import DataUpdateCoordinator



@dataclass
class LaifenData:
    """Data for the Laifen integration."""

    title: str
    device: Laifen
    coordinator: DataUpdateCoordinator

DEVICE_REGISTRY: dict[str, dict[str, LaifenData]] = {}
DEVICE_SIGNAL = "laifen_ble_device_ready"


def laifen_device_info(device: Laifen) -> dict:
    """
    Build a per-device DeviceInfo dict.

    Uses the device's actual advertised BLE name (e.g. "LFTB01-P-FD07" or
    "LFTB02-S-412B") for both the HA device name and model, so that
    multiple Laifen devices (e.g. different toothbrushes on the same HA
    instance) show up as distinct devices instead of all being merged
    into a single generic "Laifen Toothbrush" device — which made their
    identically-named "Power" switches indistinguishable in the UI.
    """
    from .const import DOMAIN
    name = device.name or "Laifen Toothbrush"
    return {
        "identifiers":  {(DOMAIN, device.address)},
        "manufacturer": "Laifen",
        "name":         name,
        "model":        name,
        "sw_version":   "1.0.0",
    }
