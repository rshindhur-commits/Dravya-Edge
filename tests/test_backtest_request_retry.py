"""What ``request_with_retry`` treats as an answer, and what it retries.

The distinction is the whole point of the function: a status that describes the
*request* is final, and a status that describes the server's ability to answer
is not. Getting it wrong in either direction is expensive -- retrying a 401
burns the budget four times over on a request that cannot succeed, and failing
to retry a 502 aborts a run that had hours of work behind it.
"""

import unittest
from unittest.mock import patch

from app.backtesting.historical_market_data import (
    HistoricalDataError,
    request_with_retry,
)


class _Response:

    def __init__(self, status_code, text="body"):

        self.status_code = status_code
        self.text = text


def _run(responses):
    """Drive the function over a scripted sequence of replies."""

    calls = []

    def _get(url, params=None, timeout=None):

        calls.append(url)

        reply = responses[len(calls) - 1]

        if isinstance(reply, Exception):

            raise reply

        return reply

    # Patched so the test does not actually sleep through the backoff.
    with patch(
        "app.backtesting.historical_market_data.requests.get", _get
    ), patch("app.backtesting.historical_market_data.time.sleep"):

        return request_with_retry("http://x", {}, context="ctx"), len(calls)


class RequestRetryTests(unittest.TestCase):

    def test_a_gateway_error_is_retried_not_raised(self):
        """The regression: one 502 used to abort an entire replay."""

        response, calls = _run([_Response(502), _Response(200)])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, 2)

    def test_every_5xx_is_retryable(self):

        for status in [500, 502, 503, 504]:

            response, calls = _run([_Response(status), _Response(200)])

            self.assertEqual(response.status_code, 200, f"status {status}")
            self.assertEqual(calls, 2, f"status {status}")

    def test_rate_limit_and_request_timeout_are_retried(self):

        for status in [408, 429]:

            response, calls = _run([_Response(status), _Response(200)])

            self.assertEqual(response.status_code, 200, f"status {status}")
            self.assertEqual(calls, 2, f"status {status}")

    def test_a_client_error_is_final_and_is_not_retried(self):
        """401/403/404 describe the request; repeating it changes nothing."""

        for status in [400, 401, 403, 404]:

            with self.assertRaises(HistoricalDataError) as caught:

                _run([_Response(status), _Response(200)])

            self.assertIn(str(status), str(caught.exception))

    def test_transport_faults_are_still_retried(self):

        import requests

        response, calls = _run(
            [
                requests.exceptions.ConnectionError("dropped"),
                requests.exceptions.Timeout("slow"),
                _Response(200),
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, 3)

    def test_exhausting_retries_reports_the_status_that_kept_failing(self):
        """It used to report `None`, turning a rate limit into a mystery."""

        with self.assertRaises(HistoricalDataError) as caught:

            _run([_Response(429, "slow down")] * 4)

        message = str(caught.exception)

        self.assertIn("429", message)
        self.assertIn("slow down", message)
        self.assertNotIn("None", message)

    def test_it_gives_up_rather_than_retrying_forever(self):

        with self.assertRaises(HistoricalDataError):

            _run([_Response(502)] * 4)


if __name__ == "__main__":

    unittest.main()
