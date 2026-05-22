"""
Read-side helpers for the media hub.

Browse / search / tag all delegate to the in-process media_index — bots
can't call Telegram's getHistory or search methods, so the catalogue is
maintained ourselves (see main/utils/media_index.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


PAGE_SIZE = 24


@dataclass
class ExternalSubtitle:
    """A sidecar .srt/.vtt uploaded to BIN_CHANNEL alongside a video."""
    bin_message_id: int
    secure_hash: str  # for validating /sub/.../ext-{id}.vtt requests
    language: str = ""
    label: str = ""


@dataclass
class HubItem:
    message_id: int
    secure_hash: str
    title: str
    year: Optional[int]
    description: str
    tags: List[str]
    duration: int
    file_size: int
    has_thumb: bool
    quality: str = ""  # parsed resolution bucket: 480p / 720p / 1080p / 4K / ""
    file_name: str = ""  # original media filename, retained for sidecar matching
    subtitles: List[ExternalSubtitle] = field(default_factory=list)
    series_key: str = ""           # slug; "" for movies/standalone uploads
    series_title: str = ""         # human-friendly series name
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_end: Optional[int] = None  # set for multi-ep files (e.g. S01E01-E03)
    # Slug shared by every upload of the same film (different filenames /
    # release groups). "" for series episodes and uniquely-titled uploads.
    movie_key: str = ""
    # --- TMDB enrichment (optional; populated by the enrich pipeline) ---
    tmdb_id: Optional[int] = None
    tmdb_kind: str = ""            # "movie" or "tv", "" if unenriched
    imdb_id: str = ""
    poster_path: str = ""          # TMDB relative path; prepend image base
    backdrop_path: str = ""
    overview: str = ""
    tmdb_genres: List[str] = field(default_factory=list)
    enriched_at: float = 0.0       # unix ts; 0 means never attempted
    # --- ffprobe-derived codec info (optional; populated by codec_probe) -----
    # ``video_codec`` is e.g. "h264" / "hevc" / "av1"; ``pix_fmt`` is the
    # pixel format (e.g. "yuv420p" for 8-bit, "yuv420p10le" for 10-bit).
    # ``probed_at`` is the unix ts of the last probe attempt; 0 means
    # never probed.
    video_codec: str = ""
    pix_fmt: str = ""
    probed_at: float = 0.0
    # --- TMDB per-episode metadata (only meaningful for TV) -----------------
    # Populated by enrich_one / enrich_with_tmdb_id when both season and
    # episode are known and a successful /tv/{id}/season/{S} payload was
    # available. Empty strings when not fetched yet.
    episode_title: str = ""
    episode_overview: str = ""
    episode_still_path: str = ""
    episode_air_date: str = ""
    trailer_key: str = ""          # YouTube video ID; "" if not available


@dataclass
class SeriesGroup:
    """A virtual hub entry collapsing all episodes of one series."""
    series_key: str
    series_title: str
    episode_count: int
    season_count: int
    latest_message_id: int  # for newest-first ordering of the hub page
    poster_item: "HubItem"  # representative episode used for thumb/year/tags
    has_thumb: bool = False


@dataclass
class MovieGroup:
    """A virtual hub entry collapsing multiple uploads of the same movie."""
    movie_key: str
    title: str
    year: Optional[int]
    variant_count: int
    latest_message_id: int   # for newest-first ordering of the hub page
    poster_item: "HubItem"
    has_thumb: bool = False
    total_size: int = 0      # sum of variant file sizes — used for "largest" sort


# Imports kept at the bottom to avoid a circular import with media_index,
# which itself imports HubItem from this module.
from main.utils import media_index  # noqa: E402


async def query(
    *,
    q: str = "",
    year: Optional[int] = None,
    quality: str = "",
    tag: str = "",
    sort: str = "newest",
    before_id: Optional[int] = None,
    limit: int = PAGE_SIZE,
) -> Tuple[List[HubItem], Optional[int]]:
    return media_index.query(
        q=q, year=year, quality=quality, tag=tag, sort=sort,
        before_id=before_id, limit=limit,
    )


# Back-compat helpers used by tests / external callers; keep thin.
async def browse(before_id: Optional[int] = None, limit: int = PAGE_SIZE
                 ) -> Tuple[List[HubItem], Optional[int]]:
    return await query(before_id=before_id, limit=limit)


async def search(q: str, limit: int = PAGE_SIZE) -> List[HubItem]:
    items, _ = await query(q=q, limit=limit)
    return items


async def by_tag(tag: str, limit: int = PAGE_SIZE) -> List[HubItem]:
    items, _ = await query(tag=tag, limit=limit)
    return items
