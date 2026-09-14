import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler import Crawler, LinkParser, load_seeds, normalize_url


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

    def test_parser_extracts_title_and_links(self):
        parser = LinkParser()
        parser.feed("<title>  Hello <b>world</b> </title><a href='/a'>A</a><a href='x'>X</a>")
        self.assertEqual(parser.title, "Hello world")
        self.assertEqual(parser.links, ["/a", "x"])

    def test_seed_file_is_valid_json(self):
        with (Path(__file__).parents[1] / "seed_urls.json").open(encoding="utf-8") as handle:
            self.assertIsInstance(json.load(handle), list)

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


if __name__ == "__main__":
    unittest.main()
