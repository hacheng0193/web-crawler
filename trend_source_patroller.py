#!/usr/bin/env python3
"""Trend source patroller: crawl gtrends + yt-trends with no page cap.

Same behavior as trend_crawler.py (global QPS limiter, depth 2, optional
--same-site-only), but there is no max-pages limit. The run ends when the
frontier is drained and no fetch is in flight, or at the runtime cap.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

from crawler import HostAwareFrontier
from trend_crawler import ALLOWED_DOMAIN, DEFAULT_MAX_DEPTH, DEFAULT_QPS, TREND_SEEDS, TrendCrawler

NO_PAGE_LIMIT = sys.maxsize
DEFAULT_RUNTIME_SECONDS = 24 * 3600


class TrackingFrontier(HostAwareFrontier):
    """Frontier that also counts URLs handed to workers but not yet finished."""

    def __init__(self) -> None:
        super().__init__()
        self._in_flight = 0

    def get(self, timeout: float | None = None):
        item = super().get(timeout)
        with self._condition:
            self._in_flight += 1
        return item

    def task_done(self) -> None:
        with self._condition:
            self._in_flight -= 1

    def idle(self) -> bool:
        with self._condition:
            return self._size == 0 and self._in_flight == 0


class TrendSourcePatroller(TrendCrawler):
    def __init__(self, seeds: list[dict[str, Any]], *args: Any, **kwargs: Any) -> None:
        kwargs["max_pages"] = NO_PAGE_LIMIT
        super().__init__([], *args, **kwargs)
        # Swap in the tracking frontier before any seed is scheduled.
        self.frontier = TrackingFrontier()
        for seed in seeds:
            if self.schedule(str(seed["url"]), int(seed.get("priority", 5)), 0, None, event="seed"):
                self.seed_count += 1

    def _emit(self, message: str) -> None:
        super()._emit(message.replace(f"/{NO_PAGE_LIMIT}]", "]"))

    def _stop_when_idle(self) -> None:
        # Workers poll an empty frontier forever, so detect "nothing left" here.
        while not self.stop_event.wait(1.0):
            if self.frontier.idle():
                self.stop_event.set()

    def run(self) -> dict[str, Any]:
        threading.Thread(target=self._stop_when_idle, name="patrol-idle-monitor", daemon=True).start()
        return super().run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Patrol gtrends + yt-trends iamrohit.in sites with no page cap.")
    parser.add_argument("--output-dir", type=Path, default=Path(f"output-patrol-{int(time.time())}"))
    parser.add_argument("--qps", type=float, default=DEFAULT_QPS, help="global requests per second; default: 0.2")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--runtime-seconds", type=int, default=DEFAULT_RUNTIME_SECONDS, help="hard runtime cap; default: 24h")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--same-site-only", action="store_true", help=f"only crawl {ALLOWED_DOMAIN} and its subdomains")
    parser.add_argument("--seed-url", action="append", help="override the default seeds; repeatable")
    parser.add_argument("--no-live", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Path("metrics").mkdir(exist_ok=True)
    seeds = [{"url": u, "priority": 10} for u in args.seed_url] if args.seed_url else TREND_SEEDS
    summary = TrendSourcePatroller(
        seeds, args.output_dir, args.runtime_seconds, workers=args.workers,
        per_host_delay=0.0, timeout=args.timeout, max_depth=args.max_depth,
        live_output=not args.no_live, qps=args.qps, same_site_only=args.same_site_only,
    ).run()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
