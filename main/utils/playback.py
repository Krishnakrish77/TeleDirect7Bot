"""Shared playback policy helpers for classic and React players."""

from __future__ import annotations

from pathlib import Path


BROWSER_NATIVE_VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/quicktime",  # .mov
    "video/x-m4v",
    "video/webm",
}

BROWSER_NATIVE_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
}


def should_offer_hls_for_video(*, full_mime: str = "", file_name: str = "") -> bool:
    """Return True when the video container should be routed through HLS."""
    mime = (full_mime or "").lower().strip()
    if mime:
        return mime.split("/", 1)[0] == "video" and mime not in BROWSER_NATIVE_VIDEO_MIME_TYPES

    ext = Path(file_name or "").suffix.lower()
    if ext:
        return ext not in BROWSER_NATIVE_VIDEO_EXTENSIONS

    # Unknown video containers are allowed to try HLS; the HLS route still
    # probes codec/container compatibility before serving a manifest.
    return True
