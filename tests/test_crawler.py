import json
import unittest
from pathlib import Path

from crawler import LinkParser, load_seeds, normalize_url


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


if __name__ == "__main__":
    unittest.main()
