#!/usr/bin/env python3
"""Unit tests for log_analyzer's Loki windowing. Stdlib unittest, no deps.

Run: python3 -m unittest automation.test_log_analyzer   (from _tark/)
  or: python3 automation/test_log_analyzer.py

Regression guard for the 2026-06-13 windowing bug: loki_sum once ran a query_range
with step=since and SUMMED every sample. Loki evaluates count_over_time({...}[since])
at BOTH endpoints (now-since AND now), so the sum spanned ~2x the window and
double-counted -- surfacing a resolved incident (aburg-prelive redis timeouts, bounded
2026-06-11..12, zero in the real last-24h) as 3674 ongoing. The fix: a single instant
query (/loki/api/v1/query). These tests fail if anyone reverts to the range+sum path.
"""

import unittest

import log_analyzer as la


class LokiSumWindowing(unittest.TestCase):
    CREDS = {"LOKI_PASSWORD": "x"}

    def _patch_get(self, response):
        """Replace la._get with a capture+canned-response stub; returns the captured-URLs list."""
        seen = []

        def fake_get(url, user, pw, timeout=90):
            seen.append(url)
            return response

        self._orig_get = la._get
        la._get = fake_get
        self.addCleanup(lambda: setattr(la, "_get", self._orig_get))
        return seen

    def test_uses_instant_endpoint_not_range(self):
        # The core guard: loki_sum must hit the INSTANT endpoint, never query_range.
        seen = self._patch_get({"status": "success", "data": {"result": []}})
        la.loki_sum(self.CREDS, 'sum(count_over_time({x="y"}[24h]))', "24h")
        self.assertEqual(len(seen), 1)
        self.assertIn("/loki/api/v1/query?", seen[0])
        self.assertNotIn("query_range", seen[0])  # range+sum == the double-count bug

    def test_parses_single_value_per_series(self):
        # Instant (vector) results carry ONE [ts, value] pair per series under "value".
        resp = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"exc": "redis.exceptions.TimeoutError"},
                        "value": [1234, "0"],
                    },
                    {
                        "metric": {"exc": "psycopg2.OperationalError"},
                        "value": [1234, "29"],
                    },
                ]
            },
        }
        self._patch_get(resp)
        rows = la.loki_sum(self.CREDS, "q", "24h")
        # sorted desc by count; the clean (0) series sorts last -- no inflation.
        self.assertEqual(rows[0], (29, {"exc": "psycopg2.OperationalError"}))
        self.assertEqual(rows[1][0], 0)

    def test_does_not_sum_a_legacy_values_matrix(self):
        # If a range-style matrix response (multi-sample "values", no "value") ever reaches
        # loki_sum, the instant parser reads "value" only -> total 0, never the summed 2x count.
        resp = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"exc": "redis.exceptions.TimeoutError"},
                        "values": [[1, "1837"], [2, "1837"]],
                    },  # the old buggy 2-sample shape == 3674
                ]
            },
        }
        self._patch_get(resp)
        rows = la.loki_sum(self.CREDS, "q", "24h")
        self.assertEqual(rows[0][0], 0)  # NOT 3674

    def test_malformed_value_is_zero_not_crash(self):
        resp = {
            "status": "success",
            "data": {
                "result": [
                    {"metric": {"env": "demo"}, "value": [1, "NaN"]},
                    {"metric": {"env": "ionix"}},  # no value key at all
                ]
            },
        }
        self._patch_get(resp)
        rows = la.loki_sum(self.CREDS, "q", "24h")
        self.assertTrue(all(n == 0 for n, _ in rows))


if __name__ == "__main__":
    unittest.main()
