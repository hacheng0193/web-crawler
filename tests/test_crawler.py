import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from crawler import Crawler, HostAwareFrontier, LinkParser, load_seeds, normalize_url


class CrawlerTests(unittest.TestCase):
    def test_seed_file_has_exactly_100_entries(self):
        seeds = load_seeds(Path(__file__).parents[1] / "seed_urls.json")
        self.assertEqual(len(seeds), 100)
        self.assertEqual(len({seed["url"] for seed in seeds}), 100)

    def test_normalize_url_removes_fragments_and_tracking(self):
        self.assertEqual(
            normalize_url("/docs//intro/?utm_source=test&x=1#part", "HTTPS://Example.com/base"),
            "https://example.com/docs/intro?x=1",
        )
        self.assertIsNone(normalize_url("mailto:test@example.com"))
        self.assertIsNone(normalize_url("https://user:pass@example.com/"))

    def test_normalize_url_percent_encodes_unicode_path(self):
        self.assertEqual(
            normalize_url("https://example.com/中文頁面/hello world"),
            "https://example.com/%E4%B8%AD%E6%96%87%E9%A0%81%E9%9D%A2/hello%20world",
        )

    def test_parser_extracts_title_and_links(self):
        parser = LinkParser()
        parser.feed("<title>  Hello <b>world</b> </title><a href='/a'>A</a><a href='x'>X</a>")
        self.assertEqual(parser.title, "Hello world")
        self.assertEqual(parser.links, ["/a", "x"])

    def test_frontier_round_robins_hosts_while_preserving_host_priority(self):
        frontier = HostAwareFrontier()
        urls = [
            "https://a.example/1",
            "https://a.example/2",
            "https://b.example/1",
            "https://b.example/2",
            "https://c.example/1",
            "https://c.example/2",
        ]
        for order, url in enumerate(urls):
            frontier.put((-10, order, url, 1, "https://parent.example/"))

        selected = [frontier.get(timeout=0)[2].split("/")[2] for _ in range(6)]
        self.assertEqual(selected, ["a.example", "b.example", "c.example", "a.example", "b.example", "c.example"])

    def test_seed_file_is_valid_json(self):
        with (Path(__file__).parents[1] / "seed_urls.json").open(encoding="utf-8") as handle:
            self.assertIsInstance(json.load(handle), list)

    def test_generated_seed_sets_are_nested_and_exact_size(self):
        root = Path(__file__).parents[1]
        sets = []
        for size in (300, 500, 1000):
            with (root / f"seed_urls_{size}.json").open(encoding="utf-8") as handle:
                seeds = json.load(handle)
            self.assertEqual(len(load_seeds(root / f"seed_urls_{size}.json")), size)
            self.assertEqual(len(seeds), size)
            self.assertEqual(len({seed["url"] for seed in seeds}), size)
            self.assertTrue(all(seed["url"] for seed in seeds))
            sets.append(seeds)

        self.assertEqual([seed["url"] for seed in sets[0]], [seed["url"] for seed in sets[1][:300]])
        self.assertEqual([seed["url"] for seed in sets[1]], [seed["url"] for seed in sets[2][:500]])

    def test_discovered_and_crawled_are_persisted_separately(self):
        seeds = [{"url": "https://example.com/", "priority": 10}]
        html = '<title>Example</title><a href="/next">Next</a>'
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Crawler, "_allowed_by_robots", return_value=True), patch.object(
                Crawler, "_fetch", return_value=(200, "text/html", html, None)
            ):
                summary = Crawler(
                    seeds,
                    Path(directory),
                    runtime_seconds=2,
                    max_pages=1,
                    workers=1,
                    per_host_delay=0,
                    timeout=1,
                    max_depth=1,
                    live_output=False,
                ).run()

            discovered_lines = (Path(directory) / "discovered.jsonl").read_text(encoding="utf-8").splitlines()
            crawled_lines = (Path(directory) / "crawled.jsonl").read_text(encoding="utf-8").splitlines()
            pending = json.loads((Path(directory) / "discovered_pending.json").read_text(encoding="utf-8"))

            self.assertEqual(summary["total_discovered"], 2)
            self.assertEqual(summary["successful_crawled"], 1)
            self.assertEqual(len(discovered_lines), 2)
            self.assertEqual(len(crawled_lines), 1)
            self.assertEqual(list(pending), ["https://example.com/next"])
            crawled_record = json.loads(crawled_lines[0])
            request_started_at = datetime.fromisoformat(crawled_record["request_started_at"])
            fetched_at = datetime.fromisoformat(crawled_record["fetched_at"])
            self.assertLessEqual(request_started_at, fetched_at)

    def test_worker_records_unexpected_errors_without_stopping(self):
        seeds = [
            {"url": "https://example.com/中文頁面", "priority": 10},
            {"url": "https://example.org/ok", "priority": 10},
        ]

        def fetch(url):
            if "example.com" in url:
                raise UnicodeEncodeError("ascii", url, 1, 2, "non-ASCII request target")
            return 200, "text/html", "<title>OK</title>", None

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Crawler, "_allowed_by_robots", return_value=True), patch.object(
                Crawler, "_fetch", side_effect=fetch
            ):
                summary = Crawler(
                    seeds,
                    Path(directory),
                    runtime_seconds=2,
                    max_pages=2,
                    workers=1,
                    per_host_delay=0,
                    timeout=1,
                    max_depth=0,
                    live_output=False,
                ).run()

            errors = json.loads((Path(directory) / "error.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["crawled_attempts"], 2)
            self.assertEqual(summary["successful_crawled"], 1)
            self.assertEqual(summary["failed_crawled"], 1)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["url"], "https://example.com/%E4%B8%AD%E6%96%87%E9%A0%81%E9%9D%A2")
            self.assertIn("UnicodeEncodeError", errors[0]["error"])


if __name__ == "__main__":
    unittest.main()
