import json
import math
from pathlib import Path
from typing import Any

from .boundary import data_directory

SETTINGS_FILE = "settings.json"
SETTINGS_VERSION = 1
USE_CLAUDE_KEY = "useClaude"
USE_CLAUDE_WHEN_UNSET = True
CLAUDE_MONTHLY_CAP_KEY = "claudeMonthlyCapUsd"
CLAUDE_MONTHLY_CAP_WHEN_UNSET = 10.0


class SettingsError(RuntimeError):
    pass


def settings_path() -> Path:
    return data_directory() / SETTINGS_FILE


def read_settings(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SettingsError(f"The settings file {path} cannot be read ({error.strerror}).") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise SettingsError(f"The settings file {path} is not valid JSON.") from None
    if not isinstance(data, dict) or data.get("version") != SETTINGS_VERSION:
        raise SettingsError(f"The settings file {path} must be an object with version {SETTINGS_VERSION}.")
    if not isinstance(data.get("clients", {}), dict):
        raise SettingsError(f"The settings file {path} must list clients as an object keyed by client id.")
    return data


def client_settings(client_id: str) -> tuple[Path, dict[str, Any]]:
    path = settings_path()
    data = read_settings(path)
    if data is None:
        return path, {}
    entry = data.get("clients", {}).get(client_id, {})
    if not isinstance(entry, dict):
        raise SettingsError(f"The settings for {client_id} in {path} must be an object.")
    return path, entry


def use_claude(client_id: str) -> bool:
    path, entry = client_settings(client_id)
    value = entry.get(USE_CLAUDE_KEY, USE_CLAUDE_WHEN_UNSET)
    if not isinstance(value, bool):
        raise SettingsError(f"{USE_CLAUDE_KEY} for {client_id} in {path} must be true or false.")
    return value


def claude_allowed(client_id: str) -> bool:
    try:
        return use_claude(client_id)
    except SettingsError:
        return False


def claude_monthly_cap_usd(client_id: str) -> float:
    path, entry = client_settings(client_id)
    value = entry.get(CLAUDE_MONTHLY_CAP_KEY, CLAUDE_MONTHLY_CAP_WHEN_UNSET)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise SettingsError(f"{CLAUDE_MONTHLY_CAP_KEY} for {client_id} in {path} must be a number of US dollars, 0 or more.")
    return float(value)
