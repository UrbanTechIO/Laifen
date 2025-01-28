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
