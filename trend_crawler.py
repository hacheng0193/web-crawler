#!/usr/bin/env python3
"""Trend crawler: crawl Google/YouTube trend sites to depth 2 at a global 0.2 QPS.

Reuses the Crawler from crawler.py and replaces per-host politeness with one
global rate limiter, so total outgoing requests (pages and robots.txt) across
all hosts never exceed `qps` per second (0.2 QPS = one request every 5 s).
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.robotparser import RobotFileParser

from urllib.parse import urlsplit

from crawler import Crawler, normalize_url

TREND_SEEDS = [
    {"url": "https://gtrends.iamrohit.in/", "priority": 10},
    {"url": "https://yt-trends.iamrohit.in", "priority": 10},
]
ALLOWED_DOMAIN = "iamrohit.in"
DEFAULT_QPS = 0.2
DEFAULT_MAX_DEPTH = 2


class RateLimiter:
    """Global limiter: hands out request slots spaced 1/qps seconds apart."""

    def __init__(self, qps: float, stop_event: threading.Event) -> None:
        if qps <= 0:
            raise ValueError("qps must be positive")
        self.interval = 1.0 / qps
        self.stop_event = stop_event
        self._lock = threading.Lock()
        self._next_slot = time.monotonic()

    def acquire(self) -> bool:
        """Block until a slot is available; return False if the crawl stopped."""
        with self._lock:
            slot = max(self._next_slot, time.monotonic())
            self._next_slot = slot + self.interval
        while not self.stop_event.is_set():
            remaining = slot - time.monotonic()
            if remaining <= 0:
                return True
            self.stop_event.wait(min(remaining, 0.5))
        return False


class TrendCrawler(Crawler):
    def __init__(self, *args: Any, qps: float = DEFAULT_QPS, same_site_only: bool = False, **kwargs: Any) -> None:
        self.qps = qps
        self.same_site_only = same_site_only
        self.limiter: RateLimiter | None = None
        # stop_event is created by Crawler.__init__, but seeds are scheduled
        # inside it, so the limiter is attached lazily right after.
        super().__init__(*args, **kwargs)
        self.limiter = RateLimiter(qps, self.stop_event)

    @staticmethod
    def is_same_site(url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return host == ALLOWED_DOMAIN or host.endswith("." + ALLOWED_DOMAIN)

    def schedule(self, url: str, priority: int, depth: int, parent_url: str | None, event: str = "new") -> bool:
        if self.same_site_only:
            normalized = normalize_url(url)
            if normalized and not self.is_same_site(normalized):
                self._record_external(normalized, priority, depth, parent_url)
                return False
        return super().schedule(url, priority, depth, parent_url, event)

    def _record_external(self, url: str, priority: int, depth: int, parent_url: str | None) -> None:
        """Count an off-site URL as discovered, but never make it a crawl candidate."""
        record = {
            "url": url,
            "priority": priority,
            "depth": depth,
            "parent_url": parent_url,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "source": "external",
            "crawl_candidate": False,
        }
        with self.state_lock:
            if url in self.all_discovered:
                return
            self.all_discovered.add(url)
            self.total_discovered += 1
        self.discovered_writer.write(record)
        self._emit(f"[EXTERNAL] depth={depth} {url}")

    def _wait_for_host(self, host: str) -> None:
        # The global limiter replaces the per-host delay.
        if self.limiter and not self.limiter.acquire():
            # Never send a request without a slot once the crawl is stopping.
            raise RuntimeError("crawl stopped before request was sent")

    def _fetch_robots(self, origin: str) -> RobotFileParser | None:
        # robots.txt is a real request too, so it consumes a slot.
        if self.limiter and not self.limiter.acquire():
            return None
        return super()._fetch_robots(origin)

    def run(self) -> dict[str, Any]:
        summary = super().run()
        summary["qps"] = self.qps
        summary["same_site_only"] = self.same_site_only
        with (self.output_dir / "crawl_summary.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl gtrends/yt-trends iamrohit.in sites at a global QPS cap.")
    parser.add_argument("--output-dir", type=Path, default=Path(f"output-trend-{int(time.time())}"))
    parser.add_argument("--qps", type=float, default=DEFAULT_QPS, help="global requests per second; default: 0.2")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--max-pages", type=int, default=200, help="page cap; at 0.2 QPS, 200 pages ~ 17 min")
    parser.add_argument("--runtime-seconds", type=int, default=3600)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--same-site-only", action="store_true", help=f"only crawl {ALLOWED_DOMAIN} and its subdomains")
    parser.add_argument("--seed-url", action="append", help="override the default seeds; repeatable")
    parser.add_argument("--no-live", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Path("metrics").mkdir(exist_ok=True)
    summary = TrendCrawler(
        [{"url": u, "priority": 10} for u in args.seed_url] if args.seed_url else TREND_SEEDS, args.output_dir, args.runtime_seconds, args.max_pages, args.workers,
        per_host_delay=0.0, timeout=args.timeout, max_depth=args.max_depth,
        live_output=not args.no_live, qps=args.qps, same_site_only=args.same_site_only,
    ).run()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
