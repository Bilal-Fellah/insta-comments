"""Instagram scraper configuration (thin subclass of the shared base)."""

from __future__ import annotations

from typing import Any, ClassVar

from core.config import BaseScraperConfig

# Re-exported for callers that imported these from here historically.
from core.config import PROJECT_ROOT, resolve_default_chrome  # noqa: F401


class InstagramConfig(BaseScraperConfig):
    env_prefix: ClassVar[str] = "IG"
    default_platform: ClassVar[str] = "instagram"
    output_prefix: ClassVar[str] = "instagram"
    base_url: ClassVar[str] = "https://www.instagram.com"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstagramConfig":
        # Back-compat: accept the historical instagram_username/password YAML keys.
        data = dict(data)
        if "instagram_username" in data:
            data.setdefault("login_username", data.pop("instagram_username"))
        if "instagram_password" in data:
            data.setdefault("login_password", data.pop("instagram_password"))
        return super().from_dict(data)  # type: ignore[return-value]


# Legacy alias so `from instagram_scraper.config import ScraperConfig` still works.
ScraperConfig = InstagramConfig
