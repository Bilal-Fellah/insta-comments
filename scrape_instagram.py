#!/usr/bin/env python3
"""CLI entrypoint for Instagram comment scraper."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from instagram_scraper.config import ScraperConfig
from instagram_scraper.scraper import InstagramCommentScraper
import json
import time
from datetime import datetime, timezone, timedelta
from instagram_scraper.api_client import ScrapingApiClient
from instagram_scraper.profile import normalize_post_url


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stealth Instagram comment scraper for your own profile posts",
    )
    p.add_argument("--api", action="store_true", help="Run in API mode (integrate with Flask scraping backend)")
    p.add_argument("--config", type=Path, help="Path to YAML config file")
    p.add_argument("--profile-url", help="Instagram profile URL (scrape recent posts)")
    p.add_argument(
        "--post-url",
        action="append",
        dest="post_urls",
        metavar="URL",
        help="Instagram post/reel URL to scrape directly (repeatable)",
    )
    p.add_argument("--post-limit", type=int, help="Number of recent posts to scrape")
    p.add_argument("--output-dir", type=Path, help="Output directory for JSON/CSV")
    p.add_argument("--cookies-file", type=Path, help="Exported cookies JSON")
    p.add_argument("--user-data-dir", help="Chrome user data directory")
    p.add_argument("--chrome-profile", help="Chrome profile directory name")
    p.add_argument("--proxy", help="Proxy URL, e.g. http://user:pass@host:port")
    p.add_argument("--headless", action="store_true", help="Run headless (less stealthy)")
    p.add_argument("--username", help="Instagram login username")
    p.add_argument("--password", help="Instagram login password")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def build_config(args: argparse.Namespace) -> ScraperConfig:
    cfg = ScraperConfig.from_env()
    if args.config and args.config.exists():
        file_cfg = ScraperConfig.from_yaml(args.config)
        for field_name in (
            "profile_url",
            "post_urls",
            "post_limit",
            "output_dir",
            "headless",
            "chrome_binary",
            "chromedriver_path",
            "user_data_dir",
            "chrome_profile",
            "cookies_file",
            "rotate_fingerprint",
            "user_agent",
            "proxy",
            "instagram_username",
            "instagram_password",
            "delay_min",
            "delay_max",
            "scroll_pause_min",
            "scroll_pause_max",
            "max_comment_scroll_rounds",
            "max_idle_rounds",
            "max_retries",
            "challenge_wait_seconds",
            "scraping_api_key",
            "scraping_api_url",
            "scraping_platform",
            "scraping_batch_size",
            "scraping_batch_delay_seconds",
            "scraping_start_date_days",
            "api_mode",
        ):
            if hasattr(file_cfg, field_name):
                setattr(cfg, field_name, getattr(file_cfg, field_name))
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
        instagram_username=args.username,
        instagram_password=args.password,
    )
    if args.api:
        cfg.api_mode = True
    return cfg


def to_unix_timestamp(ts) -> int:
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
    return int(time.time())


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    try:
        cfg = build_config(args)
        if cfg.api_mode:
            if not cfg.scraping_api_key:
                raise ValueError("SCRAPING_API_KEY is not configured in environment or config.")
            if not cfg.scraping_api_url:
                raise ValueError("SCRAPING_API_URL is not configured in environment or config.")

            client = ScrapingApiClient(cfg.scraping_api_url, cfg.scraping_api_key)
            
            start_date_dt = datetime.now(timezone.utc) - timedelta(days=cfg.scraping_start_date_days)
            start_date_str = start_date_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            
            logging.info("API Mode Active. Fetching posts for platform=%s, start_date=%s", cfg.scraping_platform, start_date_str)
            session_id, posts = client.fetch_posts(cfg.scraping_platform, start_date_str)
            
            if not posts:
                logging.info("No posts returned from API to scrape. Exiting.")
                return 0
                
            logging.info("Fetched %d posts from API. Session ID: %s", len(posts), session_id)
            
            batch_size = cfg.scraping_batch_size
            batches = [posts[i:i + batch_size] for i in range(0, len(posts), batch_size)]
            logging.info("Split into %d batch(es) of max size %d.", len(batches), batch_size)
            
            for b_idx, batch in enumerate(batches, start=1):
                logging.info("Starting session for batch %d/%d (containing %d posts)", b_idx, len(batches), len(batch))
                
                url_mapping = {}
                batch_urls = []
                for p in batch:
                    p_url = p.get("url")
                    if not p_url:
                        continue
                    try:
                        norm_url = normalize_post_url(p_url)
                        url_mapping[norm_url] = (p.get("post_id"), p.get("page_id"))
                        batch_urls.append(norm_url)
                    except Exception as exc:
                        logging.warning("Skipping invalid post URL %s: %s", p_url, exc)
                
                if not batch_urls:
                    logging.warning("No valid URLs in batch %d. Skipping.", b_idx)
                    continue
                
                batch_cfg = ScraperConfig.from_dict(vars(cfg))
                for field_name in vars(cfg):
                    setattr(batch_cfg, field_name, getattr(cfg, field_name))
                batch_cfg.post_urls = batch_urls
                batch_cfg.profile_url = ""
                
                scraper = InstagramCommentScraper(batch_cfg)
                json_paths = scraper.run()
                
                for json_path_str in json_paths:
                    logging.info("Reading scraped results from %s", json_path_str)
                    with open(json_path_str, "r", encoding="utf-8") as f:
                        scraped_data = json.load(f)
                    
                    comments_to_insert = []
                    for post_res in scraped_data.get("posts", []):
                        res_url = post_res.get("post_url")
                        try:
                            norm_res_url = normalize_post_url(res_url)
                        except Exception:
                            norm_res_url = res_url
                        
                        post_id, page_id = url_mapping.get(norm_res_url, (None, None))
                        if not post_id or not page_id:
                            logging.warning("Could not map scraped post URL %s to page_id/post_id. Skipping comments.", res_url)
                            continue
                        
                        for comment in post_res.get("comments", []):
                            unix_ts = to_unix_timestamp(comment.get("timestamp"))
                            
                            cid = comment.get("id")
                            if not cid:
                                import hashlib
                                text_hash = hashlib.md5(f"{comment.get('username')}:{comment.get('text')}:{unix_ts}".encode("utf-8")).hexdigest()[:16]
                                cid = f"gen_{text_hash}"
                            
                            comment_payload = {
                                "page_id": page_id,
                                "platform": "instagram",
                                "post_id": post_id,
                                "id": str(cid),
                                "text": comment.get("text"),
                                "username": comment.get("username"),
                                "timestamp": unix_ts,
                                "likes": comment.get("likes", 0),
                                "is_reply": bool(comment.get("is_reply")),
                            }
                            if comment.get("parent_id"):
                                comment_payload["parent_id"] = str(comment.get("parent_id"))
                                
                            comments_to_insert.append(comment_payload)
                    
                    logging.info("Submitting %d comments to Flask API.", len(comments_to_insert))
                    insert_res = client.insert_comments(session_id, comments_to_insert)
                    logging.info(
                        "Insert Result - Inserted: %s, Skipped: %s, Total: %s",
                        insert_res.get("inserted"),
                        insert_res.get("skipped"),
                        insert_res.get("total")
                    )
                
                try:
                    session_details = client.get_session_details(session_id)
                    logging.info(
                        "Session Status: %s, Posts Fetched: %s, Comments Inserted: %s",
                        session_details.get("status"),
                        session_details.get("posts_fetched"),
                        session_details.get("comments_inserted")
                    )
                except Exception as api_exc:
                    logging.warning("Could not fetch session details: %s", api_exc)
                
                if b_idx < len(batches) and cfg.scraping_batch_delay_seconds > 0:
                    logging.info(
                        "Batch %d/%d complete. Waiting %d seconds before starting next batch...",
                        b_idx,
                        len(batches),
                        cfg.scraping_batch_delay_seconds
                    )
                    time.sleep(cfg.scraping_batch_delay_seconds)
            
            try:
                logging.info("Marking session %s as completed.", session_id)
                comp_res = client.complete_session(session_id)
                logging.info("Session completion result: %s", comp_res)
            except Exception as api_exc:
                logging.warning("Could not complete session: %s", api_exc)

            logging.info("Flask API Scraping Flow Complete.")
            return 0
        else:
            scraper = InstagramCommentScraper(cfg)
            json_paths = scraper.run()
            print("Done. Saved JSON files:")
            for p in json_paths:
                print(f"  {p}")
            return 0
    except Exception as exc:  # noqa: BLE001
        logging.error("Fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
