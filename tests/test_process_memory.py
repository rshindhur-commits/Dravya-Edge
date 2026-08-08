"""Memory telemetry must never be the reason a scan fails.

It is attached to the record of a completed scan, so every failure mode here --
wrong platform, unreadable /proc, a garbage value -- has to come back as
"no reading" rather than an exception.
"""

from unittest.mock import mock_open, patch

from app.runtime.process_memory import memory_snapshot, resident_mb


def test_a_non_linux_platform_says_nothing_rather_than_guessing():

    with patch("sys.platform", "win32"):

        assert resident_mb() is None
        assert memory_snapshot() == {}


def test_linux_reads_the_resident_pages():

    # statm: size resident shared text lib data dt
    with patch("sys.platform", "linux"), \
            patch("builtins.open", mock_open(read_data="12345 2560 100 1 0 900 0")), \
            patch("app.runtime.process_memory._page_size", return_value=4096):

        # 2560 pages x 4096 bytes = 10.5MB
        assert resident_mb() == 10.5
        assert memory_snapshot() == {"rss_mb": 10.5}


def test_the_page_size_falls_back_where_sysconf_will_not_answer():
    """Windows has no sysconf; the Linux path must still be reachable in tests."""

    from app.runtime.process_memory import _PAGE_SIZE_FALLBACK, _page_size

    with patch("os.sysconf", side_effect=AttributeError, create=True):

        assert _page_size() == _PAGE_SIZE_FALLBACK


def test_an_unreadable_proc_is_not_an_error():

    with patch("sys.platform", "linux"), \
            patch("builtins.open", side_effect=OSError("no /proc")):

        assert resident_mb() is None
        assert memory_snapshot() == {}


def test_a_malformed_statm_is_not_an_error():

    with patch("sys.platform", "linux"), \
            patch("builtins.open", mock_open(read_data="not-a-number")):

        assert resident_mb() is None
        assert memory_snapshot() == {}
