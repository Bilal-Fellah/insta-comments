"""Flask scraping-backend API client."""

from __future__ import annotations

import logging
import requests
from typing import Any

logger = logging.getLogger(__name__)


class ScrapingApiClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def fetch_posts(self, platform: str, start_date: str | None = None) -> tuple[str, list[dict[str, Any]]]:
        """Fetch posts for scraping.

        GET /api/scraping/posts
        """
        url = f"{self.base_url}/api/scraping/posts"
        params = {"platform": platform}
        if start_date:
            params["start_date"] = start_date

        logger.info("Fetching posts from API: %s with params=%s", url, params)
        try:
            resp = requests.get(url, params=params, headers=self.headers, timeout=30)
            if resp.status_code == 401:
                logger.error("API Error (401): Invalid or missing API key.")
                resp.raise_for_status()
            elif resp.status_code == 429:
                logger.error("API Error (429): Rate limit exceeded.")
                resp.raise_for_status()
            elif resp.status_code != 200:
                logger.error("API Error (%d): %s", resp.status_code, resp.text)
                resp.raise_for_status()

            payload = resp.json()
            if not payload.get("success"):
                raise RuntimeError(f"API returned success=False: {payload.get('error')}")

            data = payload.get("data") or {}
            session_id = data.get("session_id")
            posts = data.get("posts") or []
            return session_id, posts
        except Exception as exc:
            logger.error("Failed to fetch posts from API: %s", exc)
            raise

    def insert_comments(
        self,
        session_id: str | None,
        comments: list[dict[str, Any]],
        post_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Insert scraped comments.

        POST /api/scraping/comments

        ``post_results`` lists every post that was actually scraped (as
        {page_id, platform, post_id}), including posts that yielded zero
        comments, so the backend records them as done instead of re-serving
        them on the next fetch.
        """
        url = f"{self.base_url}/api/scraping/comments"
        payload = {
            "comments": comments
        }
        if session_id:
            payload["session_id"] = session_id
        if post_results:
            payload["post_results"] = post_results

        logger.info("Inserting %d comments to API: %s", len(comments), url)
        try:
            resp = requests.post(url, json=payload, headers=self.headers, timeout=60)
            if resp.status_code == 400:
                logger.error("API Validation Error (400): %s", resp.text)
                resp.raise_for_status()
            elif resp.status_code == 401:
                logger.error("API Error (401): Invalid or missing API key.")
                resp.raise_for_status()
            elif resp.status_code == 429:
                logger.error("API Error (429): Rate limit exceeded.")
                resp.raise_for_status()
            elif resp.status_code != 200:
                logger.error("API Error (%d): %s", resp.status_code, resp.text)
                resp.raise_for_status()

            res_payload = resp.json()
            if not res_payload.get("success"):
                raise RuntimeError(f"API returned success=False: {res_payload.get('error')}")

            return res_payload.get("data") or {}
        except Exception as exc:
            logger.error("Failed to insert comments to API: %s", exc)
            raise

    def get_session_details(self, session_id: str) -> dict[str, Any]:
        """Retrieve details about a specific session.

        GET /api/scraping/sessions/{session_id}
        """
        url = f"{self.base_url}/api/scraping/sessions/{session_id}"
        logger.info("Fetching session details from API: %s", url)
        try:
            resp = requests.get(url, headers=self.headers, timeout=30)
            if resp.status_code == 401:
                logger.error("API Error (401): Invalid or missing API key.")
                resp.raise_for_status()
            elif resp.status_code == 429:
                logger.error("API Error (429): Rate limit exceeded.")
                resp.raise_for_status()
            elif resp.status_code != 200:
                logger.error("API Error (%d): %s", resp.status_code, resp.text)
                resp.raise_for_status()

            payload = resp.json()
            if not payload.get("success"):
                raise RuntimeError(f"API returned success=False: {payload.get('error')}")

            return payload.get("data") or {}
        except Exception as exc:
            logger.error("Failed to fetch session details from API: %s", exc)
            raise

    def complete_session(self, session_id: str) -> dict[str, Any]:
        """Mark a scraping session as completed.

        POST /api/scraping/sessions/{session_id}/complete
        """
        url = f"{self.base_url}/api/scraping/sessions/{session_id}/complete"
        logger.info("Completing session via API: %s", url)
        try:
            resp = requests.post(url, headers=self.headers, timeout=30)
            if resp.status_code == 400:
                logger.error("API Validation Error (400): %s", resp.text)
                resp.raise_for_status()
            elif resp.status_code == 401:
                logger.error("API Error (401): Invalid or missing API key.")
                resp.raise_for_status()
            elif resp.status_code == 429:
                logger.error("API Error (429): Rate limit exceeded.")
                resp.raise_for_status()
            elif resp.status_code != 200:
                logger.error("API Error (%d): %s", resp.status_code, resp.text)
                resp.raise_for_status()

            res_payload = resp.json()
            if not res_payload.get("success"):
                raise RuntimeError(f"API returned success=False: {res_payload.get('error')}")

            return res_payload.get("data") or {}
        except Exception as exc:
            logger.error("Failed to complete session via API: %s", exc)
            raise
