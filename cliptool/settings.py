"""App settings made in the editor's Settings window (API keys and preferences).

Stored in ``settings.json`` in the ClipTool data folder (Documents\\ClipTool for the
installed app). API keys are encrypted with Windows' own per-user protection
(DPAPI), so the file is useless on another PC or user account. On other systems
they are only obscured, and the file is made readable by its owner only.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from cliptool.logger import get_logger

log = get_logger("settings")

# name -> environment variable, label, what it unlocks, where to get one
SERVICES: dict[str, dict[str, str]] = {
    "anthropic": {
        "env": "ANTHROPIC_API_KEY", "label": "Claude (Anthropic)",
        "used_for": "AI Editor (edits your video from a chat), Auto Edit planning, smarter clip picks, "
                    "titles/descriptions/hashtags, review notes",
        "url": "https://console.anthropic.com/settings/keys",
    },
    "elevenlabs": {
        "env": "ELEVENLABS_API_KEY", "label": "ElevenLabs",
        "used_for": "Generate sound effects and background music from a description",
        "url": "https://elevenlabs.io/app/settings/api-keys",
    },
}

DEFAULT_PREFS: dict[str, Any] = {
    "claude_model": "claude-opus-5-5",
    "claude_effort": "medium",
    "whisper_model": "small",        # tiny / base / small / medium / large-v3
    "whisper_language": "",          # "" = detect
    "whisper_device": "auto",        # auto / cpu / cuda
    # Auto Edit defaults
    "tighten_keep_pause": 0.12,      # seconds of pause kept at every cut
    "tighten_min_gap": 0.30,         # pauses shorter than this are left alone
    "remove_fillers": True,          # um, uh, erm...
    "remove_restarts": True,         # repeated words / false starts
    "punch_ins": True,               # alternate zoom at cuts to hide jumps
    "bleep_swears": False,
    "music_volume": 0.15,            # ~ -16 dB under the voice
    "loudness_lufs": -14,
    "export_name_pattern": "{project}_{preset}_{date}",
}

_lock = threading.Lock()
_applied: set[str] = set()  # keys this module put into os.environ


def settings_file() -> Path:
    from cliptool.config import APP_ROOT
    home = os.environ.get("CLIPTOOL_HOME")
    return (Path(home) if home else APP_ROOT) / "settings.json"


# ---- key protection ----------------------------------------------------------------
def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    entropy = b"ClipTool"
    ebuf = ctypes.create_string_buffer(entropy, len(entropy))
    blob_e = BLOB(len(entropy), ctypes.cast(ebuf, ctypes.POINTER(ctypes.c_char)))
    if encrypt:
        ok = fn(ctypes.byref(blob_in), "ClipTool API key", ctypes.byref(blob_e), None, None, 0x1,
                ctypes.byref(blob_out))
    else:
        ok = fn(ctypes.byref(blob_in), None, ctypes.byref(blob_e), None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        raise OSError("Windows could not protect/unprotect the key")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect(secret: str) -> dict:
    raw = secret.encode("utf-8")
    if os.name == "nt":
        try:
            return {"enc": "dpapi", "data": base64.b64encode(_dpapi(raw, True)).decode()}
        except Exception as exc:  # pragma: no cover - Windows only
            log.warning("Could not encrypt the key with Windows (%s); storing it obscured.", exc)
    return {"enc": "b64", "data": base64.b64encode(bytes(b ^ 0x5A for b in raw)).decode()}


def unprotect(item: dict) -> Optional[str]:
    try:
        data = base64.b64decode(item.get("data", ""))
        if item.get("enc") == "dpapi":
            return _dpapi(data, False).decode("utf-8")
        return bytes(b ^ 0x5A for b in data).decode("utf-8")
    except Exception as exc:
        log.warning("A saved API key could not be read (%s). Enter it again in Settings.", exc)
        return None


def mask(secret: str) -> str:
    s = secret.strip()
    return (s[:6] + "…" + s[-4:]) if len(s) > 12 else "•" * len(s)


# ---- load / save -----------------------------------------------------------------------
def _read(path: Optional[Path] = None) -> dict:
    f = path or settings_file()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict, path: Optional[Path] = None) -> None:
    f = path or settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    tmp.replace(f)


def prefs(path: Optional[Path] = None) -> dict:
    out = copy.deepcopy(DEFAULT_PREFS)
    out.update({k: v for k, v in _read(path).get("prefs", {}).items() if k in DEFAULT_PREFS})
    return out


def get_key(name: str, path: Optional[Path] = None) -> Optional[str]:
    """The key saved in Settings, else the environment variable."""
    item = _read(path).get("keys", {}).get(name)
    if item:
        key = unprotect(item)
        if key:
            return key
    return os.environ.get(SERVICES[name]["env"]) or None


def public_view(path: Optional[Path] = None) -> dict:
    """What the Settings window shows: never the keys themselves."""
    saved = _read(path).get("keys", {})
    keys = {}
    for name, svc in SERVICES.items():
        key = unprotect(saved[name]) if name in saved else None
        env = os.environ.get(svc["env"]) if not key else None
        keys[name] = {**svc, "set": bool(key or env), "saved": bool(key),
                      "from_env": bool(env and not key), "hint": mask(key or env or "")}
    return {"keys": keys, "prefs": prefs(path), "file": str(path or settings_file())}


def update(changes: dict, path: Optional[Path] = None) -> dict:
    """changes = {"keys": {name: "new key" | "" (remove)}, "prefs": {...}}"""
    with _lock:
        data = _read(path)
        keys = data.setdefault("keys", {})
        for name, value in (changes.get("keys") or {}).items():
            if name not in SERVICES or value is None:
                continue
            value = str(value).strip()
            if value:
                keys[name] = protect(value)
            else:
                keys.pop(name, None)
                if name in _applied:  # forget the key we put in the environment
                    os.environ.pop(SERVICES[name]["env"], None)
                    _applied.discard(name)
        p = data.setdefault("prefs", {})
        for k, v in (changes.get("prefs") or {}).items():
            if k not in DEFAULT_PREFS:
                continue
            default = DEFAULT_PREFS[k]
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, (int, float)) and not isinstance(default, bool):
                try:
                    v = type(default)(float(v)) if isinstance(default, float) else int(float(v))
                except (TypeError, ValueError):
                    continue
            else:
                v = str(v)
            p[k] = v
        _write(data, path)
    apply_env(path)
    return public_view(path)


def apply_env(path: Optional[Path] = None) -> None:
    """Make saved keys visible to the libraries that read environment variables."""
    for name, item in _read(path).get("keys", {}).items():
        if name in SERVICES:
            key = unprotect(item)
            if key:
                os.environ[SERVICES[name]["env"]] = key
                _applied.add(name)


def apply_to_config(cfg) -> None:
    """Speech-to-text preferences from Settings override config.yaml."""
    saved = _read().get("prefs", {})  # only what was changed in Settings
    pr = prefs()
    mapping = {"whisper_model": ("transcription.model", lambda v: v or "small"),
               "whisper_language": ("transcription.language", lambda v: v or None),
               "whisper_device": ("transcription.device", lambda v: v or "auto"),
               "claude_model": ("ai.model", lambda v: v),
               "claude_effort": ("ai.effort", lambda v: v)}
    try:
        for k, (dotted, conv) in mapping.items():
            if k in saved:
                cfg.set_path(dotted, conv(pr[k]))
    except Exception as exc:  # pragma: no cover
        log.debug("could not apply settings to config: %s", exc)


# ---- "Test" buttons ---------------------------------------------------------------------------
def test_key(name: str, key: Optional[str] = None) -> tuple[bool, str]:
    key = (key or "").strip() or get_key(name)
    if not key:
        return False, "No key entered."
    if name == "anthropic":
        try:
            import anthropic
        except ImportError:
            return False, "The 'anthropic' package is not installed (pip install anthropic)."
        try:
            client = anthropic.Anthropic(api_key=key, max_retries=1, timeout=20)
            model = prefs()["claude_model"]
            info = client.models.retrieve(model)
            return True, f"Key works. Model: {getattr(info, 'display_name', None) or model}"
        except anthropic.AuthenticationError:
            return False, "This key was rejected (wrong or revoked key)."
        except anthropic.NotFoundError:
            return True, "Key works, but the chosen Claude model isn't available to it. Pick another model."
        except Exception as exc:
            return False, f"Could not reach Anthropic: {exc}"
    if name == "elevenlabs":
        import urllib.error
        import urllib.request
        req = urllib.request.Request("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
            return True, "Key works."
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return False, "This key was rejected (wrong key or missing permission)."
            return False, f"ElevenLabs answered {exc.code}."
        except Exception as exc:
            return False, f"Could not reach ElevenLabs: {exc}"
    return False, "Unknown service."
