import json
import logging
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORAGE_DIR = Path("/config/.storage")


def save_device_state(address: str, result: dict[str, Any]) -> None:
    """Save the device's last known state to a file."""
    try:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        path = STORAGE_DIR / f"laifen_{address.replace(':', '')}.json"
        with path.open("w") as f:
            json.dump(result, f)
        _LOGGER.debug(f"Saved Laifen state for {address} to {path}")
    except Exception as e:
        _LOGGER.error(f"Failed to save Laifen state for {address}: {e}")


def load_device_state(address: str) -> dict[str, Any] | None:
    """Load a device's last known state from file."""
    try:
        path = STORAGE_DIR / f"laifen_{address.replace(':', '')}.json"
        if path.exists():
            with path.open() as f:
                data = json.load(f)
                _LOGGER.debug(f"Loaded cached Laifen state for {address} from {path}")
                return data
        else:
            _LOGGER.debug(f"No cached state file found for {address} at {path}")
    except Exception as e:
        _LOGGER.error(f"Failed to load Laifen state for {address}: {e}")
    return None
