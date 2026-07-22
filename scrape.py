#!/usr/bin/env python3
"""Unified CLI entrypoint for the stealth social-comment scrapers.

    python scrape.py --platform instagram --post-url <URL>
    python scrape.py --platform facebook  --profile-url <URL> --post-limit 3
    python scrape.py --platform facebook  --api
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import fields
from pathlib import Path
from typing import Callable

from core.base_scraper import BaseCommentScraper
from core.config import BaseScraperConfig

PLATFORM_CHOICES = ("instagram", "facebook")


def get_platform(name: str) -> tuple[type[BaseScraperConfig], type[BaseCommentScraper], Callable[[str], str]]:
    """Lazily resolve the config class, scraper class and URL normalizer."""
    if name == "instagram":
        from instagram_scraper.config import InstagramConfig
        from instagram_scraper.scraper import InstagramCommentScraper
        from instagram_scraper.profile import normalize_post_url

        return InstagramConfig, InstagramCommentScraper, normalize_post_url
    if name == "facebook":
        from facebook_scraper.config import FacebookConfig
        from facebook_scraper.scraper import FacebookCommentScraper
        from facebook_scraper.profile import normalize_post_url

        return FacebookConfig, FacebookCommentScraper, normalize_post_url
    raise ValueError(f"Unknown platform: {name!r} (choose from {PLATFORM_CHOICES})")


def parse_args(default_platform: str | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stealth Instagram/Facebook comment scraper")
    p.add_argument(
        "--platform",
        choices=PLATFORM_CHOICES,
        default=default_platform,
        help="Which platform to scrape",
    )
    p.add_argument("--api", action="store_true", help="Run in API mode (Flask scraping backend)")
    p.add_argument("--config", type=Path, help="Path to YAML config file")
    p.add_argument("--profile-url", help="Profile/page URL (scrape recent posts)")
    p.add_argument(
        "--post-url",
        action="append",
        dest="post_urls",
        metavar="URL",
        help="Post/reel/permalink URL to scrape directly (repeatable)",
    )
    p.add_argument("--post-limit", type=int, help="Number of recent posts to scrape")
    p.add_argument("--output-dir", type=Path, help="Output directory for JSON")
    p.add_argument("--cookies-file", type=Path, help="Exported cookies JSON")
    p.add_argument("--user-data-dir", help="Chrome user data directory")
    p.add_argument("--chrome-profile", help="Chrome profile directory name")
    p.add_argument("--proxy", help="Proxy URL, e.g. http://user:pass@host:port")
    p.add_argument("--headless", action="store_true", default=None, help="Run headless (less stealthy)")
    p.add_argument("--username", help="Login username/email")
    p.add_argument("--password", help="Login password")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def build_config(args: argparse.Namespace, config_cls: type[BaseScraperConfig]) -> BaseScraperConfig:
    cfg = config_cls.from_env()
    if args.config and args.config.exists():
        file_cfg = config_cls.from_yaml(args.config)
        for f in fields(config_cls):
            setattr(cfg, f.name, getattr(file_cfg, f.name))
    cfg.apply_env_overrides()
    cfg.merge_cli(
        profile_url=args.profile_url,
        post_urls=args.post_urls,
        post_limit=args.post_limit,
        output_dir=args.output_dir,
        cookies_file=args.cookies_file,
        user_data_dir=args.user_data_dir,
        chrome_profile=args.chrome_profile,
        proxy=args.proxy,
        headless=args.headless,
        login_username=args.username,
        login_password=args.password,
    )
    if args.api:
        cfg.api_mode = True
    return cfg


def main(default_platform: str | None = None) -> int:
    args = parse_args(default_platform)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    platform = args.platform or default_platform
    if not platform:
        logging.error("No platform selected. Pass --platform {instagram,facebook}.")
        return 2

    try:
        config_cls, scraper_cls, normalize_post_url = get_platform(platform)
        cfg = build_config(args, config_cls)

        if cfg.api_mode:
            from core.api_flow import run_api_flow

            return run_api_flow(cfg, scraper_cls, normalize_post_url)

        scraper = scraper_cls(cfg)
        json_paths = scraper.run()
        print("Done. Saved JSON files:")
        for path in json_paths:
            print(f"  {path}")
        return 0
    except Exception as exc:  # noqa: BLE001
        logging.error("Fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
