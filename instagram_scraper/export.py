"""Export scraped data to JSON and CSV."""

from __future__ import annotations

import csv
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
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    prefix = "post" if mode == "post" else "instagram"
    base = output_dir / f"{prefix}_{label}_{stamp}"

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

