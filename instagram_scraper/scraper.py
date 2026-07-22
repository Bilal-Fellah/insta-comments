"""Instagram scraper — implements the BaseCommentScraper hooks."""

from __future__ import annotations

import logging

from core.base_scraper import BaseCommentScraper
from core.models import PostRef

from .auth import ensure_session, is_challenge_page
from .comments import scrape_post_comments
from .config import ScraperConfig
from .interceptor_config import INTERCEPTOR_JS
from .profile import collect_recent_posts, extract_username, posts_from_urls

logger = logging.getLogger(__name__)


class InstagramCommentScraper(BaseCommentScraper):
    output_prefix = "instagram"

    def __init__(self, config: ScraperConfig):
        super().__init__(config)

    def interceptor_scripts(self) -> list[str]:
        return [INTERCEPTOR_JS]

    def ensure_session(self, driver) -> None:
        ensure_session(driver, self.config)

    def is_challenge(self, driver) -> bool:
        return is_challenge_page(driver)

    def challenge_message(self) -> str:
        return "Instagram challenge page active; resolve manually or refresh cookies"

    def posts_from_urls(self, urls: list[str]) -> list[PostRef]:
        return posts_from_urls(urls)

    def discover_posts(self, driver) -> list[PostRef]:
        return collect_recent_posts(driver, self.config)

    def scrape_post(self, driver, post: PostRef) -> dict:
        return scrape_post_comments(
            driver,
            self.config,
            post.url,
            media_id=post.media_id,
            post_meta=post.metadata,
        )

    def profile_label(self) -> str:
        return extract_username(self.config.profile_url)
