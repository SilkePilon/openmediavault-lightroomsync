"""Folder and file name templates.

Templates use `{token}` placeholders. Folder templates may contain `/` to
create nested directories; empty segments (e.g. `{album}` when syncing the
whole library) are dropped.
"""

import datetime
import os
import re
from typing import Dict, Optional

from .config import ConfigError

TOKENS = {
    "year": "Capture year, e.g. 2024",
    "month": "Capture month, 01-12",
    "monthname": "Capture month name, e.g. March",
    "day": "Capture day, 01-31",
    "hour": "Capture hour, 00-23",
    "minute": "Capture minute",
    "second": "Capture second",
    "name": "Original file name without extension",
    "id": "Lightroom asset ID",
    "id8": "First 8 characters of the asset ID",
    "album": "Album name (album sync only)",
    "albumpath": "Album name including parent folders (album sync only)",
}
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
_UNSAFE_RE = re.compile(r'[\x00-\x1f/\\:*?"<>|]')
MAX_COMPONENT_BYTES = 200
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")


def validate_template(template: str, what: str):
    for token in _TOKEN_RE.findall(template):
        if token not in TOKENS:
            raise ConfigError(f"Unknown token {{{token}}} in {what}; valid tokens: "
                              + ", ".join("{%s}" % t for t in TOKENS))


def sanitize(component: str) -> str:
    component = _UNSAFE_RE.sub("_", component).strip().strip(".").strip()
    encoded = component.encode("utf-8")
    if len(encoded) > MAX_COMPONENT_BYTES:
        component = encoded[:MAX_COMPONENT_BYTES].decode("utf-8", "ignore").rstrip()
    return component


def capture_datetime(asset: dict) -> datetime.datetime:
    """Best-effort capture time; Lightroom reports `0000-00-00T00:00:00`
    when unknown, so fall back to the import and creation times."""
    payload = asset.get("payload", {})
    candidates = (
        payload.get("captureDate"),
        payload.get("importSource", {}).get("importTimestamp"),
        asset.get("created"),
    )
    for value in candidates:
        dt = _parse_date(value)
        if dt:
            return dt
    return datetime.datetime(1970, 1, 1)


def _parse_date(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value or value.startswith("0000"):
        return None
    value = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.datetime.fromisoformat(value[:19])
        except ValueError:
            return None
    # Capture dates are wall-clock time; keep them naive.
    return dt.replace(tzinfo=None) if dt.year > 1 else None


def asset_tokens(asset: dict, album_name: str = "", album_path: str = "") -> Dict[str, str]:
    dt = capture_datetime(asset)
    filename = asset.get("payload", {}).get("importSource", {}).get("fileName", "")
    name = os.path.splitext(filename)[0] or asset["id"][:8]
    return {
        "year": f"{dt.year:04d}",
        "month": f"{dt.month:02d}",
        "monthname": MONTH_NAMES[dt.month - 1],
        "day": f"{dt.day:02d}",
        "hour": f"{dt.hour:02d}",
        "minute": f"{dt.minute:02d}",
        "second": f"{dt.second:02d}",
        "name": name,
        "id": asset["id"],
        "id8": asset["id"][:8],
        "album": album_name,
        "albumpath": album_path,
    }


def _render(template: str, tokens: Dict[str, str]) -> str:
    return _TOKEN_RE.sub(lambda m: tokens.get(m.group(1), ""), template)


def render_relpath(folder_template: str, filename_template: str,
                   tokens: Dict[str, str], ext: str = ".jpg") -> str:
    # Values are sanitized per segment so a `/` inside a value cannot create
    # extra directories. A segment that is exactly `{albumpath}` is the
    # intentional exception: it mirrors the album folder hierarchy.
    parts = []
    for segment in folder_template.split("/"):
        if segment.strip() == "{albumpath}":
            parts.extend(sanitize(p) for p in tokens["albumpath"].split("/"))
        else:
            parts.append(sanitize(_render(segment, tokens)))
    parts = [p for p in parts if p]
    stem = sanitize(_render(filename_template, tokens)) or tokens["id8"]
    return os.path.join(*parts, stem + ext) if parts else stem + ext


def with_suffix(relpath: str, n: int) -> str:
    root, ext = os.path.splitext(relpath)
    return f"{root}_{n}{ext}"
