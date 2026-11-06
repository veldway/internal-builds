"""Tests for metrics, rendering and the narrative fallback paths.

Run from weekly-client-report/:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import metrics
import narrate
import render

SAMPLE = pathlib.Path(__file__).resolve().parent.parent / "sample_data" / "acme_outdoor_week45.json"


def sample_data():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


class MetricsTest(unittest.TestCase):
    def test_sample_numbers_add_up(self):
        m = metrics.compute(sample_data())
        # 14 orders, 1 refunded → 13 kept; revenue is the sum of kept totals.
        self.assertEqual(m["orders"], 13)
        self.assertEqual(m["refund_count"], 1)
        self.assertEqual(m["refund_value"], 42.50)
        expected_revenue = round(sum(
            o["total"] for o in sample_data()["orders"] if not o["refunded"]), 2)
        self.assertEqual(m["revenue"], expected_revenue)
        self.assertEqual(m["aov"], round(expected_revenue / 13, 2))
        self.assertEqual(m["new_orders"] + m["returning_orders"], 13)

    def test_top_product_ordering(self):
        m = metrics.compute(sample_data())
        revenues = [p["revenue"] for p in m["top_products"]]
        self.assertEqual(revenues, sorted(revenues, reverse=True))
        self.assertLessEqual(len(m["top_products"]), 5)

    def test_wow_deltas_match_baseline(self):
        m = metrics.compute(sample_data())
        prev = sample_data()["previous_week"]
        expected = round((m["revenue"] - prev["revenue"]) / prev["revenue"] * 100, 1)
        self.assertEqual(m["deltas"]["revenue_pct"], expected)

    def test_no_baseline_gives_none_not_crash(self):
        data = sample_data()
        del data["previous_week"]
        m = metrics.compute(data)
        self.assertIsNone(m["deltas"]["revenue_pct"])

    def test_validation_rejects_bad_input(self):
        data = sample_data()
        del data["orders"][0]["total"]
        with self.assertRaises(metrics.InputError):
            metrics.compute(data)
        with self.assertRaises(metrics.InputError):
            metrics.compute({"store": "x", "currency": "USD", "week": {}, "orders": []})

    def test_duplicate_order_ids_rejected(self):
        data = sample_data()
        data["orders"][1]["id"] = data["orders"][0]["id"]
        with self.assertRaises(metrics.InputError):
            metrics.compute(data)

    def test_revenue_is_charged_amounts_with_no_double_discount(self):
        # total is post-discount, so revenue = sum of kept totals exactly;
        # the discount figure is informational and must not be subtracted again.
        m = metrics.compute(sample_data())
        kept_totals = round(sum(
            o["total"] for o in sample_data()["orders"] if not o["refunded"]), 2)
        self.assertEqual(m["revenue"], kept_totals)
        self.assertEqual(m["discount_value"], 77.40)
        discounted = next(o for o in sample_data()["orders"] if o["id"] == "A-1002")
        list_price = sum(i["qty"] * i["price"] for i in discounted["items"])
        self.assertEqual(discounted["total"], round(list_price - discounted["discount"], 2))

    def test_week_subkeys_required(self):
        data = sample_data()
        del data["week"]["label"]
        with self.assertRaises(metrics.InputError):
            metrics.compute(data)

    def test_malformed_item_is_refused_not_crashed(self):
        data = sample_data()
        del data["orders"][0]["items"][0]["price"]
        with self.assertRaises(metrics.InputError):
            metrics.compute(data)
        data = sample_data()
        data["orders"][0]["items"] = []
        with self.assertRaises(metrics.InputError):
            metrics.compute(data)


class NarrateTest(unittest.TestCase):
    def test_mock_is_deterministic_and_labeled(self):
        m = metrics.compute(sample_data())
        one, src1 = narrate.narrative(m, mock=True)
        two, src2 = narrate.narrative(m, mock=True)
        self.assertEqual(one, two)
        self.assertEqual((src1, src2), ("mock", "mock"))
        self.assertIn("[mock narrative]", one)

    def test_no_api_key_falls_back(self):
        m = metrics.compute(sample_data())
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            text, source = narrate.narrative(m)
        self.assertEqual(source, "fallback")
        self.assertIn(str(m["orders"]), text)

    def test_api_failure_falls_back_after_retries(self):
        m = metrics.compute(sample_data())
        boom = urllib.error.URLError("connection refused")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
             mock.patch.object(narrate, "_request", side_effect=boom) as fake, \
             mock.patch.object(narrate.time, "sleep") as slept:
            text, source = narrate.narrative(m)
        self.assertEqual(source, "fallback")
        self.assertEqual(fake.call_count, narrate.RETRIES)
        self.assertEqual(slept.call_count, narrate.RETRIES - 1)
        self.assertTrue(text)

    def test_non_retryable_http_error_stops_early(self):
        m = metrics.compute(sample_data())
        err = urllib.error.HTTPError("url", 401, "unauthorized", None, None)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "bad-key"}), \
             mock.patch.object(narrate, "_request", side_effect=err) as fake, \
             mock.patch.object(narrate.time, "sleep"):
            _, source = narrate.narrative(m)
        self.assertEqual(source, "fallback")
        self.assertEqual(fake.call_count, 1)  # a 401 is not retried

    def test_trickling_or_oversized_body_is_bounded_and_falls_back(self):
        m = metrics.compute(sample_data())

        class EndlessResponse:
            def read1(self, n):
                return b"x" * n  # never ends: must hit the size cap, not hang

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
             mock.patch.object(narrate.urllib.request, "urlopen",
                               return_value=EndlessResponse()) as fake, \
             mock.patch.object(narrate.time, "sleep"):
            text, source = narrate.narrative(m)
        self.assertEqual(source, "fallback")
        self.assertEqual(fake.call_count, narrate.RETRIES)
        self.assertTrue(text)

    def test_fallback_mentions_flags(self):
        m = metrics.compute(sample_data())
        if not m["flags"]:
            m["flags"] = ["orders down 20% week-over-week"]
        text = narrate.fallback_narrative(m)
        self.assertIn("Worth noting", text)


class RenderTest(unittest.TestCase):
    def test_html_contains_key_numbers_and_escapes(self):
        m = metrics.compute(sample_data())
        m["store"] = 'Acme <script>alert(1)</script>'
        html_out = render.to_html(m, "Narrative & summary.", "mock")
        self.assertNotIn("<script>alert(1)</script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)
        self.assertIn(f"{m['revenue']:,.2f}", html_out)
        self.assertIn("Narrative &amp; summary.", html_out)

    def test_week_dates_and_currency_are_escaped(self):
        m = metrics.compute(sample_data())
        m["week"]["start"] = '<img src=x onerror=alert(1)>'
        m["currency"] = '<b>USD</b>'
        html_out = render.to_html(m, "n.", "mock")
        self.assertNotIn("<img src=x", html_out)
        self.assertNotIn("<b>USD</b>", html_out)
        self.assertIn("&lt;img", html_out)
        self.assertIn("&lt;b&gt;USD&lt;/b&gt;", html_out)

    def test_text_report_lists_top_products(self):
        m = metrics.compute(sample_data())
        text = render.to_text(m, "n.")
        for product in m["top_products"]:
            self.assertIn(product["title"], text)

    def test_slug_is_filesystem_safe(self):
        self.assertEqual(render.slug("Acme Outdoor Supply (sample)"), "acme-outdoor-supply-sample")


if __name__ == "__main__":
    unittest.main()
