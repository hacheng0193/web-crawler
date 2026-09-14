#!/usr/bin/env python3
"""Small, polite web crawler MVP for a search-engine seed experiment."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import Request, build_opener
from urllib.robotparser import RobotFileParser

USER_AGENT = "SeedSearchCrawler/0.1 (+https://example.com/crawler-info)"
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_", "source", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term"}


def normalize_url(raw_url: str, base_url: str | None = None) -> str | None:
    """Return a crawlable canonical-ish URL, or None for unsupported links."""
    if base_url:
        raw_url = urljoin(base_url, raw_url)
    raw_url, _ = urldefrag(raw_url.strip())
    parts = urlsplit(raw_url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host
    if parts.username or parts.password:
        return None
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    if path != "/":
        path = re.sub(r"/{2,}", "/", path)
        path = path.rstrip("/") or "/"
    query = urlencode([(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in TRACKING_PARAMS])
    return urlunsplit((scheme, netloc, path, query, ""))


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag.lower() == "a" and attrs_map.get("href"):
            self.links.append(attrs_map["href"] or "")
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        raw_title = re.sub(r"<[^>]+>", " ", " ".join(self.title_parts))
        return re.sub(r"\s+", " ", raw_title).strip()[:500]


@dataclass
class CrawlResult:
    url: str
    status: int | None
    title: str
    content_type: str
    depth: int
    parent_url: str | None
    links_found: int
    fetched_at: str
    elapsed_ms: int
    error: str | None = None


class Crawler:
    def __init__(self, seeds: list[dict[str, Any]], output_dir: Path, runtime_seconds: int = 600, max_pages: int = 2000, workers: int = 8, per_host_delay: float = 1.0, timeout: float = 12.0, max_depth: int = 2, live_output: bool = True) -> None:
        self.output_dir = output_dir
        self.runtime_seconds = max(1, runtime_seconds)
        self.deadline = time.monotonic() + self.runtime_seconds
        self.max_pages = max_pages
        self.workers = max(1, workers)
        self.per_host_delay = max(0.0, per_host_delay)
        self.timeout = timeout
        self.max_depth = max(0, max_depth)
        self.live_output = live_output
        self.frontier: queue.PriorityQueue[tuple[int, int, str, int, str | None]] = queue.PriorityQueue()
        self.seen: set[str] = set()
        self.results: list[CrawlResult] = []
        self.host_last_fetch: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser | None] = {}
        self.counter = 0
        self.state_lock = threading.Lock()
        self.host_lock = threading.Lock()
        self.robots_lock = threading.Lock()
        self.result_lock = threading.Lock()
        self.print_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.opener = build_opener()
        self.logger = logging.getLogger("crawler")
        self.seed_count = 0
        self.claimed_pages = 0
        for seed in seeds:
            url = normalize_url(str(seed.get("url", "")))
            if url:
                self.schedule(url, int(seed.get("priority", 5)), 0, None, event="seed")
                self.seed_count += 1

    def _emit(self, message: str) -> None:
        if not self.live_output:
            return
        with self.print_lock:
            print(message, flush=True)

    def schedule(self, url: str, priority: int, depth: int, parent_url: str | None, event: str = "new") -> bool:
        normalized = normalize_url(url)
        if not normalized:
            return False
        with self.state_lock:
            if normalized in self.seen or self.stop_event.is_set():
                return False
            self.seen.add(normalized)
            self.counter += 1
            # PriorityQueue is min-first, so negate the user-facing priority.
            self.frontier.put((-priority, self.counter, normalized, depth, parent_url))
        self._emit(f"[{event.upper()}] depth={depth} {normalized}")
        return True

    def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self.robots_lock:
            if origin in self.robots:
                parser = self.robots[origin]
            else:
                parser = self._fetch_robots(origin)
                self.robots[origin] = parser
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def _fetch_robots(self, origin: str) -> RobotFileParser | None:
        robots_url = f"{origin}/robots.txt"
        request = Request(robots_url, headers={"User-Agent": USER_AGENT})
        parser = RobotFileParser(robots_url)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(256 * 1024).decode("utf-8", errors="replace")
            parser.parse(body.splitlines())
            return parser
        except HTTPError as exc:
            if exc.code in {HTTPStatus.NOT_FOUND, HTTPStatus.GONE}:
                parser.parse([])
                return parser
            self.logger.debug("robots unavailable for %s: %s", origin, exc)
        except (OSError, URLError, TimeoutError) as exc:
            self.logger.debug("robots unavailable for %s: %s", origin, exc)
        # A transient/unreachable robots endpoint should not make the entire seed unusable.
        return None

    def _wait_for_host(self, host: str) -> None:
        while not self.stop_event.is_set():
            with self.host_lock:
                wait_for = self.per_host_delay - (time.monotonic() - self.host_last_fetch.get(host, 0.0))
                if wait_for <= 0:
                    self.host_last_fetch[host] = time.monotonic()
                    return
            self.stop_event.wait(min(wait_for, 0.5))

    def _fetch(self, url: str) -> tuple[int | None, str, str, str | None]:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = getattr(response, "status", response.getcode())
                content_type = response.headers.get_content_type()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    return status, content_type, "", None
                body = response.read(2 * 1024 * 1024).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
                return status, content_type, body, None
        except HTTPError as exc:
            return exc.code, "", "", str(exc)
        except (OSError, URLError, TimeoutError) as exc:
            return None, "", "", str(exc)

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            if time.monotonic() >= self.deadline:
                self.stop_event.set()
                return
            try:
                _priority, _order, url, depth, parent_url = self.frontier.get(timeout=0.25)
            except queue.Empty:
                # Keep workers alive while another worker may still discover links.
                # The coordinator stops them at the runtime cap or page cap.
                continue
            started = time.monotonic()
            try:
                with self.result_lock:
                    if self.claimed_pages >= self.max_pages:
                        self.stop_event.set()
                        continue
                    self.claimed_pages += 1
                if not self._allowed_by_robots(url):
                    self._record(url, None, "", "", depth, parent_url, 0, started, "blocked by robots.txt")
                    continue
                host = urlsplit(url).netloc
                self._wait_for_host(host)
                status, content_type, body, error = self._fetch(url)
                parser = LinkParser()
                if body:
                    parser.feed(body)
                    if depth < self.max_depth:
                        for link in parser.links[:500]:
                            child = normalize_url(link, url)
                            if child:
                                self.schedule(child, max(1, 10 - depth), depth + 1, url)
                self._record(url, status, parser.title, content_type, depth, parent_url, len(parser.links), started, error)
            finally:
                self.frontier.task_done()

    def _record(self, url: str, status: int | None, title: str, content_type: str, depth: int, parent_url: str | None, links_found: int, started: float, error: str | None = None) -> None:
        result = CrawlResult(url, status, title, content_type, depth, parent_url, links_found, datetime.now(timezone.utc).isoformat(), int((time.monotonic() - started) * 1000), error)
        with self.result_lock:
            self.results.append(result)
            result_number = len(self.results)
        status_text = str(status) if status is not None else "ERR"
        suffix = f" error={error}" if error else ""
        self._emit(f"[FETCHED {result_number}/{self.max_pages}] status={status_text} depth={depth} {url}{suffix}")

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        started_at = datetime.now(timezone.utc)
        threads = [threading.Thread(target=self._worker, name=f"crawler-{i}", daemon=True) for i in range(self.workers)]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            if time.monotonic() >= self.deadline:
                self.stop_event.set()
                break
            time.sleep(0.1)
        self.stop_event.set()
        for thread in threads:
            thread.join(timeout=self.timeout + 1)
        elapsed = round(time.monotonic() - started, 3)
        summary = {
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "runtime_seconds": elapsed,
            "configured_runtime_seconds": self.runtime_seconds,
            "seed_count": self.seed_count,
            "pages_recorded": len(self.results),
            "frontier_remaining": self.frontier.qsize(),
            "robots_hosts_cached": len(self.robots),
            "workers": self.workers,
            "max_depth": self.max_depth,
        }
        self._write_outputs(summary)
        return summary

    def _write_outputs(self, summary: dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with (self.output_dir / "crawl_results.jsonl").open("w", encoding="utf-8") as handle:
            for result in self.results:
                handle.write(json.dumps(result.__dict__, ensure_ascii=False) + "\n")
        with (self.output_dir / "crawl_summary.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)


def load_seeds(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        seeds = json.load(handle)
    if not isinstance(seeds, list):
        raise ValueError("seed file must contain a JSON array")
    if len(seeds) != 100:
        raise ValueError(f"expected exactly 100 seeds, got {len(seeds)}")
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the 100-seed search crawler MVP.")
    parser.add_argument("--seed-file", type=Path, default=Path(__file__).with_name("seed_urls.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--runtime-seconds", type=int, default=600, help="hard runtime cap; default: 600 (10 minutes)")
    parser.add_argument("--max-pages", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--per-host-delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--no-live", action="store_true", help="disable live SEED/NEW/FETCHED URL output")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    seeds = load_seeds(args.seed_file)
    summary = Crawler(seeds, args.output_dir, args.runtime_seconds, args.max_pages, args.workers, args.per_host_delay, args.timeout, args.max_depth, live_output=not args.no_live).run()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
