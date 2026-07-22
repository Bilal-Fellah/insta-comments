"""Flask-backend 'API mode' flow (platform-agnostic).

Fetches posts from the backend, scrapes them in batches, and pushes comments
back. Identical for every platform; the caller supplies the config, an API
client, a scraper factory and the platform's ``normalize_post_url``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from .api_client import ScrapingApiClient
from .base_scraper import BaseCommentScraper
from .config import BaseScraperConfig

logger = logging.getLogger(__name__)


def to_unix_timestamp(ts) -> int | None:
    """Best-effort Unix seconds from a scraped timestamp.

    Returns ``None`` when the value can't be parsed, so callers can tell a real
    timestamp apart from an unknown one instead of silently substituting the
    current time.
    """
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str):
        if ts.isdigit():
            return int(ts)
        try:
            cleaned = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return int(dt.timestamp())
        except Exception:
            pass
    return None


def run_api_flow(
    config: BaseScraperConfig,
    scraper_factory: Callable[[BaseScraperConfig], BaseCommentScraper],
    normalize_post_url: Callable[[str], str],
) -> int:
    if not config.scraping_api_key:
        raise ValueError("SCRAPING_API_KEY is not configured in environment or config.")
    if not config.scraping_api_url:
        raise ValueError("SCRAPING_API_URL is not configured in environment or config.")

    platform = config.scraping_platform or config.default_platform
    client = ScrapingApiClient(config.scraping_api_url, config.scraping_api_key)

    start_date_dt = datetime.now(timezone.utc) - timedelta(days=config.scraping_start_date_days)
    start_date_str = start_date_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.info("API Mode Active. Fetching posts for platform=%s, start_date=%s", platform, start_date_str)
    session_id, posts = client.fetch_posts(platform, start_date_str)

    if not posts:
        logger.info("No posts returned from API to scrape. Exiting.")
        return 0

    logger.info("Fetched %d posts from API. Session ID: %s", len(posts), session_id)

    batch_size = config.scraping_batch_size
    batches = [posts[i:i + batch_size] for i in range(0, len(posts), batch_size)]
    logger.info("Split into %d batch(es) of max size %d.", len(batches), batch_size)

    for b_idx, batch in enumerate(batches, start=1):
        logger.info("Starting session for batch %d/%d (containing %d posts)", b_idx, len(batches), len(batch))

        url_mapping: dict[str, tuple[Any, Any]] = {}
        batch_urls: list[str] = []
        for p in batch:
            p_url = p.get("url")
            if not p_url:
                continue
            try:
                norm_url = normalize_post_url(p_url)
                url_mapping[norm_url] = (p.get("post_id"), p.get("page_id"))
                batch_urls.append(norm_url)
            except Exception as exc:
                logger.warning("Skipping invalid post URL %s: %s", p_url, exc)

        if not batch_urls:
            logger.warning("No valid URLs in batch %d. Skipping.", b_idx)
            continue

        batch_cfg = replace(config, post_urls=batch_urls, profile_url="")

        # A failure inside a single batch (e.g. one post exhausting its
        # retries) must not abort the whole run — log it and move on so the
        # remaining batches still get scraped.
        try:
            scraper = scraper_factory(batch_cfg)
            json_paths = scraper.run()
        except Exception as exc:
            logger.error(
                "Batch %d/%d failed during scraping and was skipped: %s",
                b_idx, len(batches), exc,
            )
            logger.debug("Batch failure traceback:", exc_info=True)
            if b_idx < len(batches) and config.scraping_batch_delay_seconds > 0:
                time.sleep(config.scraping_batch_delay_seconds)
            continue

        for json_path_str in json_paths:
            logger.info("Reading scraped results from %s", json_path_str)
            try:
                with open(json_path_str, "r", encoding="utf-8") as f:
                    scraped_data = json.load(f)
            except Exception as exc:
                logger.error("Could not read scraped results %s: %s", json_path_str, exc)
                continue

            comments_to_insert: list[dict[str, Any]] = []
            post_results: list[dict[str, Any]] = []
            seen_posts: set[tuple[Any, Any]] = set()
            for post_res in scraped_data.get("posts", []):
                res_url = post_res.get("post_url")
                try:
                    norm_res_url = normalize_post_url(res_url)
                except Exception:
                    norm_res_url = res_url

                post_id, page_id = url_mapping.get(norm_res_url, (None, None))
                if not post_id or not page_id:
                    logger.warning("Could not map scraped post URL %s to page_id/post_id. Skipping comments.", res_url)
                    continue

                # Record that this post was scraped — even with 0 comments —
                # so the backend marks it done rather than re-serving it.
                post_key = (page_id, post_id)
                if post_key not in seen_posts:
                    seen_posts.add(post_key)
                    post_results.append({
                        "page_id": page_id,
                        "platform": platform,
                        "post_id": post_id,
                    })

                for comment in post_res.get("comments", []):
                    raw_ts = to_unix_timestamp(comment.get("timestamp"))

                    # Sanitize scraped fields up front: the backend validates
                    # strictly (text/username must be non-null strings, likes
                    # numeric) and rejects the whole batch on the first bad
                    # comment, so coerce nulls/odd types instead of losing the
                    # rest of the post's comments.
                    text = comment.get("text")
                    text = str(text) if text is not None else ""
                    username = comment.get("username")
                    username = str(username) if username is not None else ""
                    likes = comment.get("likes", 0)
                    if not isinstance(likes, (int, float)):
                        likes = 0

                    cid = comment.get("id")
                    if not cid:
                        # Generate a stable id from content-identifying fields.
                        # Only fold in the timestamp when it's actually known —
                        # never the now() fallback, which would change the id
                        # every run and cause duplicate inserts.
                        parts = [username, text]
                        if raw_ts is not None:
                            parts.append(str(raw_ts))
                        text_hash = hashlib.md5(
                            ":".join(parts).encode("utf-8")
                        ).hexdigest()[:16]
                        cid = f"gen_{text_hash}"

                    if raw_ts is None:
                        # Backend requires a numeric timestamp; fall back to
                        # scrape time as a last resort but make it visible.
                        logger.warning(
                            "Comment %s on post %s has no parseable timestamp; using scrape time.",
                            cid, post_id,
                        )
                        unix_ts = int(time.time())
                    else:
                        unix_ts = raw_ts

                    comment_payload = {
                        "page_id": page_id,
                        "platform": platform,
                        "post_id": post_id,
                        "id": str(cid),
                        "text": text,
                        "username": username,
                        "timestamp": unix_ts,
                        "likes": likes,
                        "is_reply": bool(comment.get("is_reply")),
                    }
                    if comment.get("parent_id"):
                        comment_payload["parent_id"] = str(comment.get("parent_id"))

                    comments_to_insert.append(comment_payload)

            logger.info(
                "Submitting %d comments across %d post(s) to Flask API.",
                len(comments_to_insert), len(post_results),
            )
            try:
                insert_res = client.insert_comments(
                    session_id, comments_to_insert, post_results=post_results
                )
                logger.info(
                    "Insert Result - Inserted: %s, Skipped: %s, Total: %s",
                    insert_res.get("inserted"),
                    insert_res.get("skipped"),
                    insert_res.get("total"),
                )
            except Exception as exc:
                logger.error(
                    "Failed to submit comments for batch %d/%d: %s",
                    b_idx, len(batches), exc,
                )

        try:
            session_details = client.get_session_details(session_id)
            logger.info(
                "Session Status: %s, Posts Fetched: %s, Comments Inserted: %s",
                session_details.get("status"),
                session_details.get("posts_fetched"),
                session_details.get("comments_inserted"),
            )
        except Exception as api_exc:
            logger.warning("Could not fetch session details: %s", api_exc)

        if b_idx < len(batches) and config.scraping_batch_delay_seconds > 0:
            logger.info(
                "Batch %d/%d complete. Waiting %d seconds before starting next batch...",
                b_idx,
                len(batches),
                config.scraping_batch_delay_seconds,
            )
            time.sleep(config.scraping_batch_delay_seconds)

    try:
        logger.info("Marking session %s as completed.", session_id)
        comp_res = client.complete_session(session_id)
        logger.info("Session completion result: %s", comp_res)
    except Exception as api_exc:
        logger.warning("Could not complete session: %s", api_exc)

    logger.info("API Scraping Flow Complete for platform=%s.", platform)
    return 0
