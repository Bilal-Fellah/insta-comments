"""Main orchestration."""

from __future__ import annotations

import logging
import traceback

from .auth import ensure_session, is_challenge_page
from .comments import scrape_post_comments
from .config import ScraperConfig
from .driver import build_driver
from .export import export_results
from .fingerprint import generate_fingerprint
from .human import human_delay
from .profile import collect_recent_posts, extract_username, posts_from_urls

logger = logging.getLogger(__name__)


class InstagramCommentScraper:
    def __init__(self, config: ScraperConfig):
        self.config = config

    def run(self) -> list[str]:
        self.config.validate()
        post_mode = bool(self.config.post_urls)
        if post_mode:
            posts_meta = posts_from_urls(self.config.post_urls)
            source_url = posts_meta[0].url
            label = posts_meta[0].shortcode if len(posts_meta) == 1 else f"{len(posts_meta)}_posts"
        else:
            source_url = self.config.profile_url
            label = extract_username(self.config.profile_url)

        fingerprint = generate_fingerprint(self.config.user_agent) if self.config.rotate_fingerprint else None

        driver = None
        last_error: Exception | None = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                logger.info("Starting scrape attempt %d/%d", attempt, self.config.max_retries)
                driver = build_driver(self.config, fingerprint)
                ensure_session(driver, self.config)

                if is_challenge_page(driver):
                    raise RuntimeError("Instagram challenge page active; resolve manually or refresh cookies")

                if post_mode:
                    logger.info("Post mode: scraping %d direct URL(s)", len(posts_meta))
                else:
                    posts_meta = collect_recent_posts(driver, self.config)
                    if not posts_meta:
                        raise RuntimeError(
                            f"No posts found on profile {label}. "
                            "This account may have zero posts, be private, or require manual verification."
                        )

                json_paths = []
                for idx, post in enumerate(posts_meta, start=1):
                    logger.info("Processing post %d/%d: %s", idx, len(posts_meta), post.url)
                    result = scrape_post_comments(
                        driver,
                        self.config,
                        post.url,
                        media_id=post.media_id,
                        post_meta=post.metadata,
                    )
                    result["shortcode"] = post.shortcode
                    
                    json_path = export_results(
                        post.url,
                        post.shortcode,
                        [result],
                        self.config.output_dir,
                        mode="post",
                    )
                    logger.info("Saved JSON: %s", json_path)
                    json_paths.append(str(json_path))

                    if idx < len(posts_meta):
                        human_delay(self.config.delay_min, self.config.delay_max)

                return json_paths

            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.error("Attempt %d failed: %s", attempt, exc)
                logger.debug(traceback.format_exc())
                human_delay(self.config.delay_min, self.config.delay_max)
            finally:
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:  # noqa: BLE001
                        pass
                    driver = None

        raise RuntimeError(f"Scrape failed after {self.config.max_retries} attempts: {last_error}")
