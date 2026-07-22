"""Shared data models.

Comment dict shape produced by every platform extractor::

    {
        "id": str | None,          # platform comment id (pk / fbid), None if unknown
        "username": str | None,    # author username or display name
        "text": str | None,        # comment body
        "timestamp": int | str | None,  # unix seconds or ISO string
        "likes": int,              # reaction / like count
        "is_reply": bool,          # True when nested under a parent comment
        "parent_id": str | None,   # parent comment id when is_reply
    }
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PostRef:
    """A reference to a single post/reel/story to be scraped."""

    url: str
    shortcode: str
    media_id: str | None = None
    metadata: dict | None = None
