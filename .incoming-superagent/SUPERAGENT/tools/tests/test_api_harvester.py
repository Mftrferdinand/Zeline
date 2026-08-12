import csv
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import api_harvester as H  # noqa: E402


class PaginateTest(unittest.TestCase):
    def test_offset_stops_on_short_page(self):
        data = list(range(25))
        got = list(H.paginate_offset(lambda off, lim: data[off:off + lim], limit=10))
        self.assertEqual(got, data)

    def test_offset_respects_max_pages(self):
        # infinite source: every page is full-length
        got = list(
            H.paginate_offset(
                lambda off, lim: list(range(off, off + lim)), limit=10, max_pages=2
            )
        )
        self.assertEqual(len(got), 20)

    def test_cursor_until_none(self):
        pages = {None: (["a", "b"], "c1"), "c1": (["c"], None)}
        got = list(H.paginate_cursor(lambda cur: pages[cur]))
        self.assertEqual(got, ["a", "b", "c"])


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.obj = {"data": {"items": [{"addr": "0x1"}, {"addr": "0x2"}], "n": 2}}

    def test_wildcard(self):
        self.assertEqual(H.extract(self.obj, "data.items[*].addr"), ["0x1", "0x2"])

    def test_index(self):
        self.assertEqual(H.extract(self.obj, "data.items[0].addr"), ["0x1"])

    def test_scalar(self):
        self.assertEqual(H.extract(self.obj, "data.n"), [2])

    def test_missing_returns_empty(self):
        self.assertEqual(H.extract(self.obj, "data.nope"), [])


class CurlTest(unittest.TestCase):
    def test_post_with_data_and_headers(self):
        cmd = (
            "curl 'https://api.example.com/items?p=1' "
            "-H 'Authorization: Bearer T' -H 'Accept: application/json' "
            "--data-raw '{\"q\":1}'"
        )
        spec = H.parse_curl(cmd)
        self.assertEqual(spec.method, "POST")
        self.assertEqual(spec.url, "https://api.example.com/items?p=1")
        self.assertEqual(spec.headers["Authorization"], "Bearer T")
        self.assertEqual(spec.headers["Accept"], "application/json")
        self.assertEqual(spec.data, '{"q":1}')

    def test_get_default_method(self):
        spec = H.parse_curl("curl 'https://x.io/a' -H 'Accept: */*' --compressed")
        self.assertEqual(spec.method, "GET")
        self.assertEqual(spec.url, "https://x.io/a")
        self.assertEqual(spec.headers["Accept"], "*/*")

    def test_explicit_method(self):
        spec = H.parse_curl("curl -X DELETE 'https://x.io/a/1'")
        self.assertEqual(spec.method, "DELETE")
        self.assertEqual(spec.url, "https://x.io/a/1")


class WriterTest(unittest.TestCase):
    def test_jsonl_roundtrip(self):
        rows = [{"a": 1}, {"a": 2, "b": 3}]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.jsonl")
            self.assertEqual(H.to_jsonl(rows, p), 2)
            with open(p) as fh:
                back = [json.loads(x) for x in fh.read().splitlines()]
            self.assertEqual(back, rows)

    def test_csv_roundtrip(self):
        rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.csv")
            self.assertEqual(H.to_csv(rows, p), 2)
            with open(p) as fh:
                back = list(csv.DictReader(fh))
            self.assertEqual(back[0]["a"], "1")
            self.assertEqual(back[1]["b"], "4")


if __name__ == "__main__":
    unittest.main()
