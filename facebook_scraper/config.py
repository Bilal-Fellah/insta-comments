"""Facebook scraper configuration (thin subclass of the shared base)."""

from __future__ import annotations

from typing import Any, ClassVar

from core.config import BaseScraperConfig

# Re-exported for parity with instagram_scraper.config.
from core.config import PROJECT_ROOT, resolve_default_chrome  # noqa: F401


class FacebookConfig(BaseScraperConfig):
    env_prefix: ClassVar[str] = "FB"
    default_platform: ClassVar[str] = "facebook"
    output_prefix: ClassVar[str] = "facebook"
    base_url: ClassVar[str] = "https://www.facebook.com"
    default_cookies_filename: ClassVar[str] = "facebook_cookies.json"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FacebookConfig":
        # Accept facebook_username/password YAML keys (parity with IG).
        data = dict(data)
        if "facebook_username" in data:
            data.setdefault("login_username", data.pop("facebook_username"))
        if "facebook_password" in data:
            data.setdefault("login_password", data.pop("facebook_password"))
        return super().from_dict(data)  # type: ignore[return-value]


# Parity alias.
ScraperConfig = FacebookConfig
