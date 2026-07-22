"""Export scraped data to JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def export_results(
    source_url: str,
    label: str,
    posts: list[dict[str, Any]],
    output_dir: Path,
    mode: str = "profile",
    prefix: str = "scrape",
) -> Path:
    """Write a run's posts to a timestamped JSON file and return its path.

    ``prefix`` names the file (e.g. "instagram", "facebook", or "post").
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    file_prefix = "post" if mode == "post" else prefix
    base = output_dir / f"{file_prefix}_{label}_{stamp}"

    payload = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "source_url": source_url,
        "label": label,
        "post_count": len(posts),
        "posts": posts,
    }
    if mode == "profile":
        payload["profile_url"] = source_url
        payload["username"] = label

    json_path = base.with_suffix(".json")
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    return json_path
