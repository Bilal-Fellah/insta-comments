"""Generic scrape orchestration.

``BaseCommentScraper`` owns the retry loop, driver lifecycle, fingerprint
generation and per-post export. Platform subclasses implement the hooks
(session, challenge detection, post discovery, per-post scraping).
"""

from __future__ import annotations

import logging
import traceback

from .config import BaseScraperConfig
from .driver import build_driver
from .export import export_results
from .fingerprint import generate_fingerprint
from .human import human_delay
from .models import PostRef

logger = logging.getLogger(__name__)


class BaseCommentScraper:
    #: filename prefix for exported JSON (e.g. "instagram" / "facebook")
    output_prefix: str = "scrape"

    def __init__(self, config: BaseScraperConfig):
        self.config = config

    # ------------------------------------------------------------------ hooks
    def interceptor_scripts(self) -> list[str]:
        """JS strings injected on every new document (network interceptor)."""
        return []

    def ensure_session(self, driver) -> None:
        raise NotImplementedError

    def is_challenge(self, driver) -> bool:
        return False

    def challenge_message(self) -> str:
        return "Verification challenge active; resolve manually or refresh cookies"

    def posts_from_urls(self, urls: list[str]) -> list[PostRef]:
        raise NotImplementedError

    def discover_posts(self, driver) -> list[PostRef]:
        raise NotImplementedError

    def scrape_post(self, driver, post: PostRef) -> dict:
        raise NotImplementedError

    def profile_label(self) -> str:
        return "profile"

    # ------------------------------------------------------------------ run
    def run(self) -> list[str]:
        self.config.validate()
        post_mode = bool(self.config.post_urls)
        posts_meta: list[PostRef] = []
        if post_mode:
            posts_meta = self.posts_from_urls(self.config.post_urls)

        fingerprint = (
            generate_fingerprint(self.config.user_agent) if self.config.rotate_fingerprint else None
        )
        scripts = self.interceptor_scripts()

        driver = None
        last_error: Exception | None = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                logger.info("Starting scrape attempt %d/%d", attempt, self.config.max_retries)
                driver = build_driver(self.config, fingerprint, on_new_document_scripts=scripts)
                self.ensure_session(driver)

                if self.is_challenge(driver):
                    raise RuntimeError(self.challenge_message())

                if post_mode:
                    logger.info("Post mode: scraping %d direct URL(s)", len(posts_meta))
                else:
                    posts_meta = self.discover_posts(driver)
                    if not posts_meta:
                        raise RuntimeError(
                            f"No posts found for {self.profile_label()}. The account may have zero "
                            "posts, be private, or require manual verification."
                        )

                json_paths: list[str] = []
                for idx, post in enumerate(posts_meta, start=1):
                    logger.info("Processing post %d/%d: %s", idx, len(posts_meta), post.url)
                    result = self.scrape_post(driver, post)
                    result["shortcode"] = post.shortcode

                    json_path = export_results(
                        post.url,
                        post.shortcode,
                        [result],
                        self.config.output_dir,
                        mode="post",
                        prefix=self.output_prefix,
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
