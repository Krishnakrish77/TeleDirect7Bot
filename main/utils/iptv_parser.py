"""M3U/M3U Plus parsing adapter for IPTV imports.

The third-party m3u-ipytv package exposes a playlist loader, but that loader
uses multiprocessing. Request handlers should not spawn a process pool for a
paste import, so this adapter uses the library's per-entry parser directly and
keeps the row grouping local and deterministic.
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import Any


log = logging.getLogger(__name__)

try:
    from ipytv.channel import from_playlist_entry as _ipytv_from_playlist_entry
except ImportError:
    _ipytv_from_playlist_entry = None


@dataclass
class ParsedIptvChannel:
    name: str
    stream_url: str
    logo_url: str = ""
    category: str = "Uncategorized"
    tvg_id: str = ""
    tvg_name: str = ""
    duration: str = "-1"
    attrs: dict[str, str] = field(default_factory=dict)
    extras: list[str] = field(default_factory=list)
    stream_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedIptvPlaylist:
    attrs: dict[str, str] = field(default_factory=dict)
    channels: list[ParsedIptvChannel] = field(default_factory=list)


def _clean(value: Any, max_len: int = 2000) -> str:
    return str(value or "").strip()[:max_len]


def _normalise_url(value: Any) -> str:
    url = _clean(value)
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return ""
    return url


def _parse_attrs(fragment: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if not fragment.strip():
        return attrs
    try:
        tokens = shlex.split(fragment)
    except ValueError:
        tokens = fragment.split()
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = key.strip().strip('"')
        value = value.strip().strip('"')
        if key:
            attrs[key] = value
    return attrs


def _parse_header(row: str) -> dict[str, str]:
    if not row.upper().startswith("#EXTM3U"):
        return {}
    return _parse_attrs(row[len("#EXTM3U"):])


def _stream_headers(extras: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for extra in extras:
        value = extra.strip()
        if not value.upper().startswith("#EXTVLCOPT:"):
            continue
        option = value.split(":", 1)[1].strip()
        if "=" not in option:
            continue
        key, option_value = option.split("=", 1)
        key = key.strip().lower()
        if key == "http-user-agent":
            headers["userAgent"] = option_value.strip()
        elif key == "http-referrer":
            headers["referrer"] = option_value.strip()
        else:
            headers[key] = option_value.strip()
    return headers


def _fallback_parse_entry(entry: list[str]) -> dict[str, Any]:
    extinf = next((row for row in entry if row.upper().startswith("#EXTINF")), "")
    url = next((row for row in entry if _normalise_url(row)), "")
    attrs: dict[str, str] = {}
    duration = "-1"
    name = ""
    if extinf:
        before_name, _, after_name = extinf.partition(",")
        name = after_name.strip()
        match = re.match(r"^#EXTINF:(?P<duration>[-0-9.]+)(?P<attrs>.*)$", before_name, re.IGNORECASE)
        if match:
            duration = match.group("duration") or "-1"
            attrs = _parse_attrs(match.group("attrs") or "")
    extras = [row for row in entry if row.startswith("#") and not row.upper().startswith("#EXTINF")]
    return {"name": name, "url": url, "duration": duration, "attributes": attrs, "extras": extras}


def _parse_entry(entry: list[str]) -> ParsedIptvChannel | None:
    if _ipytv_from_playlist_entry is not None:
        try:
            channel = _ipytv_from_playlist_entry(entry).to_dict()
        except Exception:
            log.exception("iptv_parser: m3u-ipytv failed to parse entry; using fallback")
            channel = _fallback_parse_entry(entry)
    else:
        channel = _fallback_parse_entry(entry)

    attrs = {str(k): _clean(v) for k, v in (channel.get("attributes") or {}).items()}
    extras = [_clean(value) for value in channel.get("extras") or [] if _clean(value)]
    stream_url = _normalise_url(channel.get("url"))
    if not stream_url:
        return None
    name = _clean(attrs.get("tvg-name") or channel.get("name"), 180)
    if not name:
        name = stream_url.rsplit("/", 1)[-1] or "Untitled channel"
    return ParsedIptvChannel(
        name=name,
        stream_url=stream_url,
        logo_url=_normalise_url(attrs.get("tvg-logo") or attrs.get("tvg-logo-small")),
        category=_clean(attrs.get("group-title"), 100) or "Uncategorized",
        tvg_id=_clean(attrs.get("tvg-id"), 180),
        tvg_name=_clean(attrs.get("tvg-name"), 180),
        duration=_clean(channel.get("duration"), 40) or "-1",
        attrs=attrs,
        extras=extras,
        stream_headers=_stream_headers(extras),
    )


def parse_m3u_text(text: str) -> ParsedIptvPlaylist:
    rows = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not rows:
        return ParsedIptvPlaylist()

    playlist = ParsedIptvPlaylist(attrs=_parse_header(rows[0]))
    entry: list[str] = []
    body = rows[1:] if rows[0].upper().startswith("#EXTM3U") else rows

    for row in body:
        upper = row.upper()
        if upper.startswith("#EXTINF"):
            if entry:
                parsed = _parse_entry(entry)
                if parsed:
                    playlist.channels.append(parsed)
            entry = [row]
            continue
        if row.startswith("#"):
            if entry:
                entry.append(row)
            continue
        if _normalise_url(row):
            if entry:
                entry.append(row)
            else:
                entry = [row]
            parsed = _parse_entry(entry)
            if parsed:
                playlist.channels.append(parsed)
            entry = []

    if entry:
        parsed = _parse_entry(entry)
        if parsed:
            playlist.channels.append(parsed)
    return playlist
