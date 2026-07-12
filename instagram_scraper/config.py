"""Configuration loading from YAML, environment variables, and CLI overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHROME = PROJECT_ROOT / ".browsers" / "chrome-linux64" / "chrome"
DEFAULT_CHROMEDRIVER = PROJECT_ROOT / ".browsers" / "chromedriver-linux64" / "chromedriver"

DOCKER_CHROME_CANDIDATES = (
    Path("/usr/bin/google-chrome-stable"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
)


def resolve_default_chrome() -> Path:
    if chrome := os.getenv("IG_CHROME_BINARY") or os.getenv("CHROME_BINARY"):
        return Path(chrome)
    for candidate in DOCKER_CHROME_CANDIDATES:
        if candidate.exists():
            return candidate
    return DEFAULT_CHROME


@dataclass
class ScraperConfig:
    profile_url: str = ""
    post_urls: list[str] = field(default_factory=list)
    post_limit: int = 10
    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "output")

    # API Integration settings
    scraping_api_key: str = ""
    scraping_api_url: str = "http://localhost:5000"
    scraping_platform: str = "instagram"
    scraping_batch_size: int = 20
    scraping_start_date_days: int = 2
    api_mode: bool = False
    scraping_batch_delay_seconds: int = 0

    # Browser / stealth
    headless: bool = False
    chrome_binary: Path = field(default_factory=resolve_default_chrome)
    chromedriver_path: Path | None = field(default_factory=lambda: DEFAULT_CHROMEDRIVER)
    user_data_dir: str | None = None
    chrome_profile: str | None = None
    cookies_file: Path | None = None

    # Fingerprint rotation
    rotate_fingerprint: bool = True
    user_agent: str | None = None
    language: str = "en-US,en"
    window_width: int = 1366
    window_height: int = 768

    # Proxy
    proxy: str | None = None  # e.g. http://user:pass@host:port

    # Login (optional — prefer cookies / profile)
    instagram_username: str | None = None
    instagram_password: str | None = None

    # Timing (seconds)
    delay_min: float = 3.0
    delay_max: float = 12.0
    scroll_pause_min: float = 0.8
    scroll_pause_max: float = 2.5
    max_comment_scroll_rounds: int = 80
    max_idle_rounds: int = 6

    # Recovery
    max_retries: int = 3
    challenge_wait_seconds: int = 120

    @classmethod
    def _resolve_path(cls, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScraperConfig":
        cfg = cls()
        for key, value in data.items():
            if not hasattr(cfg, key):
                continue
            if key in {"output_dir", "chrome_binary", "chromedriver_path", "cookies_file"}:
                if value in (None, "", "null"):
                    if key == "chromedriver_path":
                        cfg.chromedriver_path = None
                    continue
                setattr(cfg, key, cls._resolve_path(value))
                continue
            setattr(cfg, key, value)
        return cfg

    @classmethod
    def from_yaml(cls, path: Path) -> "ScraperConfig":
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_env(cls) -> "ScraperConfig":
        cfg = cls()
        if url := os.getenv("IG_PROFILE_URL"):
            cfg.profile_url = url
        if post_urls := os.getenv("IG_POST_URLS"):
            cfg.post_urls = [u.strip() for u in post_urls.split(",") if u.strip()]
        if limit := os.getenv("IG_POST_LIMIT"):
            cfg.post_limit = int(limit)
        if out := os.getenv("IG_OUTPUT_DIR"):
            cfg.output_dir = Path(out)
        if api_key := os.getenv("SCRAPING_API_KEY"):
            cfg.scraping_api_key = api_key
        if api_url := os.getenv("SCRAPING_API_URL"):
            cfg.scraping_api_url = api_url
        if platform := os.getenv("SCRAPING_PLATFORM"):
            cfg.scraping_platform = platform
        if batch_size := os.getenv("SCRAPING_BATCH_SIZE"):
            cfg.scraping_batch_size = int(batch_size)
        if batch_delay := os.getenv("SCRAPING_BATCH_DELAY_SECONDS"):
            cfg.scraping_batch_delay_seconds = int(batch_delay)
        if start_days := os.getenv("SCRAPING_START_DATE_DAYS"):
            cfg.scraping_start_date_days = int(start_days)
        if api_mode := os.getenv("SCRAPING_API_MODE"):
            cfg.api_mode = api_mode.lower() in {"1", "true", "yes"}
        if ua := os.getenv("IG_USER_AGENT"):
            cfg.user_agent = ua
        if proxy := os.getenv("IG_PROXY"):
            cfg.proxy = proxy
        if cookies := os.getenv("IG_COOKIES_FILE"):
            cfg.cookies_file = Path(cookies)
        if udd := os.getenv("IG_USER_DATA_DIR"):
            cfg.user_data_dir = None if udd.lower() in {"none", "null", "off", ""} else udd
        if prof := os.getenv("IG_CHROME_PROFILE"):
            cfg.chrome_profile = prof
        if user := os.getenv("IG_USERNAME"):
            cfg.instagram_username = user
        if pwd := os.getenv("IG_PASSWORD"):
            cfg.instagram_password = pwd
        if headless := os.getenv("IG_HEADLESS"):
            cfg.headless = headless.lower() in {"1", "true", "yes"}
        if chrome := os.getenv("IG_CHROME_BINARY") or os.getenv("CHROME_BINARY"):
            cfg.chrome_binary = Path(chrome)
        if (raw := os.getenv("IG_CHROMEDRIVER_PATH")) is not None:
            if raw.lower() in {"none", "null", "auto", ""}:
                cfg.chromedriver_path = None
            else:
                cfg.chromedriver_path = Path(raw)
        return cfg

    def apply_env_overrides(self) -> "ScraperConfig":
        """Re-apply environment overrides (useful after loading YAML in Docker)."""
        if url := os.getenv("IG_PROFILE_URL"):
            self.profile_url = url
        if post_urls := os.getenv("IG_POST_URLS"):
            self.post_urls = [u.strip() for u in post_urls.split(",") if u.strip()]
        if limit := os.getenv("IG_POST_LIMIT"):
            self.post_limit = int(limit)
        if out := os.getenv("IG_OUTPUT_DIR"):
            self.output_dir = Path(out)
        if api_key := os.getenv("SCRAPING_API_KEY"):
            self.scraping_api_key = api_key
        if api_url := os.getenv("SCRAPING_API_URL"):
            self.scraping_api_url = api_url
        if platform := os.getenv("SCRAPING_PLATFORM"):
            self.scraping_platform = platform
        if batch_size := os.getenv("SCRAPING_BATCH_SIZE"):
            self.scraping_batch_size = int(batch_size)
        if batch_delay := os.getenv("SCRAPING_BATCH_DELAY_SECONDS"):
            self.scraping_batch_delay_seconds = int(batch_delay)
        if start_days := os.getenv("SCRAPING_START_DATE_DAYS"):
            self.scraping_start_date_days = int(start_days)
        if api_mode := os.getenv("SCRAPING_API_MODE"):
            self.api_mode = api_mode.lower() in {"1", "true", "yes"}
        if proxy := os.getenv("IG_PROXY"):
            self.proxy = proxy
        if user := os.getenv("IG_USERNAME"):
            self.instagram_username = user
        if pwd := os.getenv("IG_PASSWORD"):
            self.instagram_password = pwd
        if headless := os.getenv("IG_HEADLESS"):
            self.headless = headless.lower() in {"1", "true", "yes"}
        if udd := os.getenv("IG_USER_DATA_DIR"):
            self.user_data_dir = None if udd.lower() in {"none", "null", "off", ""} else udd
        if chrome := os.getenv("IG_CHROME_BINARY") or os.getenv("CHROME_BINARY"):
            self.chrome_binary = Path(chrome)
        if (raw := os.getenv("IG_CHROMEDRIVER_PATH")) is not None:
            if raw.lower() in {"none", "null", "auto", ""}:
                self.chromedriver_path = None
            else:
                self.chromedriver_path = Path(raw)
        return self

    def merge_cli(self, **kwargs: Any) -> "ScraperConfig":
        for key, value in kwargs.items():
            if value is None:
                continue
            if key == "post_urls" and isinstance(value, list):
                self.post_urls = value
            elif key in {"output_dir", "cookies_file"}:
                setattr(self, key, Path(value))
            elif hasattr(self, key):
                setattr(self, key, value)
        return self

    def validate(self) -> None:
        if not self.api_mode and not self.profile_url and not self.post_urls:
            raise ValueError(
                "Provide profile_url (--profile-url), post_url(s) (--post-url / post_urls in config), or enable api_mode."
            )
        if not self.chrome_binary.exists():
            raise FileNotFoundError(
                f"Chrome binary not found at {self.chrome_binary}. "
                "Install Chrome or set chrome_binary in config."
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
