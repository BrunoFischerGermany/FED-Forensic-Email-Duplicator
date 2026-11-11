"""macOS: „App Translocation“ (Gatekeeper-Pfad-Randomisierung).

Ein nicht notarisiertes Programm, das mit Quarantäne-Kennzeichen (Download) gestartet wird, führt macOS
nicht am Ablageort aus, sondern aus einer schreibgeschützten Kopie unter
``/private/var/folders/…/AppTranslocation/<UUID>/d/FED.app``. Dort könnten weder ``config.toml`` noch
``FED.ini`` dauerhaft gespeichert werden. Über die Security-Schnittstelle
(``SecTranslocateCreateOriginalPathForURL``) lässt sich der echte Ablageort ermitteln.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "/AppTranslocation/"


def is_translocated(path: Path) -> bool:
    return sys.platform == "darwin" and MARKER in Path(path).as_posix()


def _original_via_security(bundle: Path) -> Path | None:
    """Echter Pfad des App-Bundles laut macOS (``None``, wenn die Schnittstelle fehlt)."""
    import ctypes
    import ctypes.util

    cf_path, sec_path = ctypes.util.find_library("CoreFoundation"), ctypes.util.find_library("Security")
    if not cf_path or not sec_path:
        return None
    cf, sec = ctypes.CDLL(cf_path), ctypes.CDLL(sec_path)
    vp = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.restype = vp
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
    cf.CFURLGetFileSystemRepresentation.restype = ctypes.c_bool
    cf.CFURLGetFileSystemRepresentation.argtypes = [vp, ctypes.c_bool, ctypes.c_char_p, ctypes.c_long]
    cf.CFRelease.argtypes = [vp]
    sec.SecTranslocateCreateOriginalPathForURL.restype = vp
    sec.SecTranslocateCreateOriginalPathForURL.argtypes = [vp, ctypes.POINTER(vp)]

    raw = str(bundle).encode()
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), True)
    if not url:
        return None
    try:
        err = vp()
        orig = sec.SecTranslocateCreateOriginalPathForURL(url, ctypes.byref(err))
        if not orig:
            if err.value:
                cf.CFRelease(err)
            return None
        try:
            buf = ctypes.create_string_buffer(4096)
            if not cf.CFURLGetFileSystemRepresentation(orig, True, buf, len(buf)):
                return None
            return Path(buf.value.decode())
        finally:
            cf.CFRelease(orig)
    finally:
        cf.CFRelease(url)


def original_bundle(bundle: Path) -> Path | None:
    """Ablageort des App-Bundles, falls macOS es verschoben ausführt; sonst ``None``."""
    if not is_translocated(bundle):
        return None
    try:
        p = _original_via_security(bundle)
    except (OSError, AttributeError, ValueError):
        return None
    return p if p and not is_translocated(p) else None


def user_data_dir() -> Path:
    """Ausweichordner, wenn der Programmordner nicht beschreibbar ist."""
    return Path.home() / "Library" / "Application Support" / "FED"
