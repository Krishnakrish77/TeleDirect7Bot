"""
Read-side helpers for the media hub.

Browse / search / tag all delegate to the in-process media_index — bots
can't call Telegram's getHistory or search methods, so the catalogue is
maintained ourselves (see main/utils/media_index.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


PAGE_SIZE = 24


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


# Imports kept at the bottom to avoid a circular import with media_index,
# which itself imports HubItem from this module.
from main.utils import media_index  # noqa: E402


async def browse(before_id: Optional[int] = None, limit: int = PAGE_SIZE
                 ) -> Tuple[List[HubItem], Optional[int]]:
    return media_index.browse_page(before_id, limit)


async def search(query: str, limit: int = PAGE_SIZE) -> List[HubItem]:
    return media_index.search(query.strip(), limit)


async def by_tag(tag: str, limit: int = PAGE_SIZE) -> List[HubItem]:
    return media_index.by_tag(tag, limit)
