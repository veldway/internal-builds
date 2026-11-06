"""Tests for the triage rules, provider chain, and output hardening.

Run from lead-intake-triage/:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import outputs
import rules
import summarize
import triage

SAMPLE = pathlib.Path(__file__).resolve().parent.parent / "sample_data" / "leads_inbox.json"


def leads():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))["leads"]


def lead_by_id(lead_id):
    return next(l for l in leads() if l["id"] == lead_id)


class RulesTest(unittest.TestCase):
    def test_hot_lead_scores_from_traceable_reasons(self):
        verdict = rules.triage(lead_by_id("L-2001"))  # Shopify + urgency + budget + long
        self.assertEqual(verdict["category"], "hot")
        self.assertGreaterEqual(verdict["score"], 70)
        self.assertTrue(any("service fit" in r for r in verdict["reasons"]))
        self.assertTrue(any("budget named" in r for r in verdict["reasons"]))
        self.assertEqual(verdict["budget"], 3000.0)

    def test_exploring_lead_is_not_hot(self):
        verdict = rules.triage(lead_by_id("L-2002"))
        self.assertIn(verdict["category"], ("nurture", "qualified"))
        self.assertLess(verdict["score"], 70)

    def test_referral_with_deadline_scores_high(self):
        verdict = rules.triage(lead_by_id("L-2003"))
        self.assertEqual(verdict["category"], "hot")
        self.assertTrue(any("referral" in r for r in verdict["reasons"]))

    def test_spam_with_injection_is_disqualified_not_scored(self):
        verdict = rules.triage(lead_by_id("L-2004"))
        self.assertEqual(verdict["category"], "spam")
        self.assertEqual(verdict["score"], 0)
        self.assertTrue(verdict["injection_suspected"])

    def test_injection_cannot_buy_a_hot_category(self):
        # The injection text literally asks for category hot, score 100.
        verdict = rules.triage(lead_by_id("L-2004"))
        self.assertNotEqual(verdict["category"], "hot")
        self.assertNotEqual(verdict["score"], 100)

    def test_budget_parsing_forms(self):
        self.assertEqual(rules.budget_amount("around $3,000 for this"), 3000.0)
        self.assertEqual(rules.budget_amount("we have $5k ready"), 5000.0)
        self.assertIsNone(rules.budget_amount("no figures here"))

    def test_budget_prefers_the_figure_named_as_budget_over_revenue(self):
        text = "We do about $80k/month in sales. Our budget is around $3,000."
        self.assertEqual(rules.budget_amount(text), 3000.0)
        # Without a budget word, the largest figure is the honest best guess.
        self.assertEqual(rules.budget_amount("$2k or maybe $4k"), 4000.0)

    def test_missing_field_and_empty_message_refused(self):
        bad = dict(lead_by_id("L-2001"))
        del bad["email"]
        with self.assertRaises(rules.InputError):
            rules.triage(bad)
        empty = dict(lead_by_id("L-2001"), message="   ")
        with self.assertRaises(rules.InputError):
            rules.triage(empty)


class ProviderChainTest(unittest.TestCase):
    def setUp(self):
        self.lead = lead_by_id("L-2001")
        self.verdict = rules.triage(self.lead)

    def test_mock_is_deterministic_and_labeled(self):
        one = summarize.prose(self.lead, self.verdict, mock=True)
        two = summarize.prose(self.lead, self.verdict, mock=True)
        self.assertEqual(one, two)
        self.assertEqual(one[2], "mock")

    def test_no_key_no_ollama_falls_back(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), \
             mock.patch.object(summarize, "ollama_host", return_value=None):
            summary, reply, source = summarize.prose(self.lead, self.verdict)
        self.assertEqual(source, "fallback")
        self.assertIn(str(self.verdict["score"]), summary)
        self.assertTrue(reply)

    def test_claude_failure_tries_ollama_then_fallback(self):
        boom = urllib.error.URLError("down")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), \
             mock.patch.object(summarize, "_post_json", side_effect=boom) as post, \
             mock.patch.object(summarize, "ollama_host", return_value="http://localhost:11434"), \
             mock.patch.object(summarize.time, "sleep"):
            _, _, source = summarize.prose(self.lead, self.verdict)
        self.assertEqual(source, "fallback")
        # Claude retried RETRIES times, then Ollama retried RETRIES times.
        self.assertEqual(post.call_count, summarize.RETRIES * 2)

    def test_claude_answer_parsed_into_summary_and_reply(self):
        answer = {"content": [{"text": "SUMMARY: They need a sync fix.\nREPLY: Hi Priya, happy to help."}]}
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), \
             mock.patch.object(summarize, "_post_json", return_value=answer):
            summary, reply, source = summarize.prose(self.lead, self.verdict)
        self.assertEqual(source, "claude")
        self.assertEqual(summary, "They need a sync fix.")
        self.assertEqual(reply, "Hi Priya, happy to help.")

    def test_spam_never_reaches_a_model(self):
        spam = lead_by_id("L-2004")
        verdict = rules.triage(spam)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), \
             mock.patch.object(summarize, "_post_json") as post:
            _, reply, source = summarize.prose(spam, verdict)
        self.assertEqual(post.call_count, 0)
        self.assertEqual(source, "fallback")
        self.assertIn("spam", reply)

    def test_oversized_body_is_bounded(self):
        class Endless:
            def read1(self, n):
                return b"x" * n

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(summarize.urllib.request, "urlopen", return_value=Endless()):
            with self.assertRaises(ValueError):
                summarize._post_json("http://x.example", {}, {})


class OutputsTest(unittest.TestCase):
    def test_formula_injection_neutralised_and_idempotent(self):
        self.assertEqual(outputs.cell_safe('=HYPERLINK("x","y")'), "'=HYPERLINK(\"x\",\"y\")")
        self.assertEqual(outputs.cell_safe(outputs.cell_safe("=1+1")), "'=1+1")
        self.assertEqual(outputs.cell_safe("plain"), "plain")

    def test_control_and_bidi_characters_made_visible(self):
        cleaned = outputs.clean_text("evil \x1b[2K\x1b[1A text ‮!")
        self.assertNotIn("\x1b", cleaned)
        self.assertNotIn("‮", cleaned)
        self.assertIn("\\u001b", cleaned)
        self.assertIn("\\u202e", cleaned)

    def test_crm_row_sanitises_the_hostile_sample(self):
        spam = lead_by_id("L-2004")
        verdict = rules.triage(spam)
        summary, reply = summarize.fallback_prose(spam, verdict)
        row = outputs.crm_row(spam, verdict, summary, reply, "fallback")
        self.assertTrue(row["company"].startswith("'="))
        self.assertNotIn("\x1b", json.dumps(row))

    def test_notification_marks_injection_and_withholds_spam_reply(self):
        spam = lead_by_id("L-2004")
        verdict = rules.triage(spam)
        note = outputs.notification(spam, verdict, "s", "r", "fallback")
        self.assertIn("prompt-injection", note)
        self.assertNotIn("HUMAN REVIEWS", note)
        hot = lead_by_id("L-2001")
        v2 = rules.triage(hot)
        note2 = outputs.notification(hot, v2, "s", "draft text", "mock")
        self.assertIn("HUMAN REVIEWS BEFORE SENDING", note2)


class EndToEndTest(unittest.TestCase):
    def test_mock_run_writes_csv_and_notifications(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = triage.main([str(SAMPLE), "--out", tmp, "--mock"])
            self.assertEqual(code, 0)
            csv_text = pathlib.Path(tmp, "crm_rows.csv").read_text(encoding="utf-8")
            notes = pathlib.Path(tmp, "notifications.txt").read_text(encoding="utf-8")
        self.assertEqual(csv_text.count("\n"), 5)  # header + 4 leads
        self.assertIn("L-2001", csv_text)
        self.assertIn("'=HYPERLINK", csv_text)   # neutralised, not executable
        self.assertNotIn("\x1b", csv_text)
        self.assertNotIn("\x1b", notes)
        self.assertIn("[HOT", notes)

    def test_duplicate_ids_skip_but_batch_survives(self):
        data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        data["leads"].append(dict(data["leads"][0]))
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp, "dup.json")
            src.write_text(json.dumps(data), encoding="utf-8")
            triage.main([str(src), "--out", tmp, "--mock"])
            notes = pathlib.Path(tmp, "notifications.txt").read_text(encoding="utf-8")
        self.assertIn("NOT TRIAGED", notes)
        self.assertIn("duplicate lead id", notes)


if __name__ == "__main__":
    unittest.main()
