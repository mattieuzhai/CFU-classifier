"""Remembering the last-used setup between launches.

Only *settings* live here — folders, the model, detection parameters, the
export choices and the view toggles. Annotations are never stored this way;
those belong in a project file, which the user saves deliberately.

Backed by QSettings, so it lands in the platform's normal preferences store
(~/Library/Preferences on macOS) and there is no file for anyone to manage.
"""

from PyQt5.QtCore import QSettings

ORG = "StJude"
APP = "CFU Annotator"

REMEMBER_KEY = "remember_settings"


def store():
    return QSettings(ORG, APP)


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def remembering():
    """Whether the user asked us to restore settings on the next launch."""
    return _as_bool(store().value(REMEMBER_KEY), default=True)


def save(values, remember=True):
    """Persist `values` (a flat dict). Clears everything when remember is off."""
    settings = store()
    settings.setValue(REMEMBER_KEY, remember)
    if not remember:
        for key in settings.allKeys():
            if key != REMEMBER_KEY:
                settings.remove(key)
        settings.sync()
        return
    for key, value in values.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()


def load():
    """Everything previously saved, as a plain dict (empty if not remembering)."""
    if not remembering():
        return {}
    settings = store()
    return {
        key: settings.value(key)
        for key in settings.allKeys()
        if key != REMEMBER_KEY
    }


def get_bool(values, key, default):
    return _as_bool(values.get(key), default=default)


def get_int(values, key, default):
    try:
        return int(values[key])
    except (KeyError, TypeError, ValueError):
        return default


def get_float(values, key, default):
    try:
        return float(values[key])
    except (KeyError, TypeError, ValueError):
        return default


def get_str(values, key, default=None):
    value = values.get(key)
    return str(value) if value else default
