import unittest
from unittest.mock import patch

from app.analytics.realtime_health import build_quote_refresh_observation
from app.options.live_options_chain import refresh_contract_quote


class RealtimeHealthTests(unittest.TestCase):

    def test_quote_refresh_observation_retains_retry_latency_and_outcome(self):

        result = build_quote_refresh_observation(
            retry_count=1,
            latency_ms=42.367,
            freshness="LIVE_QUOTE",
            refreshed_at="2026-07-23T14:00:00+00:00",
        )

        self.assertEqual(result["quote_retry_count"], 1)
        self.assertEqual(result["quote_latency_ms"], 42.37)
        self.assertEqual(result["quote_refresh_time"], "2026-07-23T14:00:00+00:00")
        self.assertEqual(result["quote_refresh_outcome"], "LIVE_QUOTE")

    def test_refresh_retries_once_until_a_live_quote_is_available(self):

        stale_quote = {"bid": 1, "ask": 1.2}
        live_quote = {"bid": 2, "ask": 2.2}

        def enrich(contract):

            updated = dict(contract)
            updated["quote_freshness"] = (
                "LIVE_QUOTE"
                if updated["bid"] == 2
                else "STALE_QUOTE"
            )
            return updated

        with patch.dict(
            "os.environ",
            {"OPTION_QUOTE_REFRESH_RETRIES": "1"},
            clear=False,
        ), patch(
            "app.options.live_options_chain.fetch_latest_option_quote",
            side_effect=[stale_quote, live_quote],
        ) as fetch_quote, patch(
            "app.options.live_options_chain._enrich_contract",
            side_effect=enrich,
        ):

            refreshed = refresh_contract_quote({"ticker": "O:NVDA"})

        self.assertEqual(fetch_quote.call_count, 2)
        self.assertEqual(refreshed["quote_retry_count"], 1)
        self.assertEqual(refreshed["quote_refresh_outcome"], "LIVE_QUOTE")
        self.assertGreaterEqual(refreshed["quote_latency_ms"], 0)


if __name__ == "__main__":

    unittest.main()