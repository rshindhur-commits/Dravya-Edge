"""How much memory this process is holding, without a new dependency.

The worker climbed roughly 350MB an hour and restarted on hitting its ceiling,
several times a session. Every diagnosis of it so far has been read off the
Render dashboard graph by eye, which is why "is it fixed" has never had a
better answer than "the line looks flatter". Nothing in the codebase recorded a
number.

psutil would do it and is not installed. It does not need to be: Linux exposes
the figure in /proc, which is where psutil reads it from, and Render is Linux.
Windows has no equivalent and returns None rather than a guess -- local runs are
not what needs watching.
"""

from __future__ import annotations

import os
import sys


# Every Linux this runs on uses 4096. The lookup is still preferred where it
# works; the constant exists so the reading survives a platform that will not
# answer, and so the Linux path stays testable from a Windows checkout.
_PAGE_SIZE_FALLBACK = 4096


def _page_size():

    try:

        return os.sysconf("SC_PAGE_SIZE")

    except (AttributeError, ValueError, OSError):

        return _PAGE_SIZE_FALLBACK


def resident_mb():
    """Resident set size in MB, or None where the platform will not say.

    Deliberately silent on failure. This is telemetry attached to a scan; it
    must never be the reason one fails.
    """

    if not sys.platform.startswith("linux"):

        return None

    try:

        # statm is a single line of page counts; the second is resident.
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            resident_pages = int(handle.read().split()[1])

        return round(resident_pages * _page_size() / 1e6, 1)

    except Exception:

        return None


def memory_snapshot():
    """Memory fields to attach to a scan's record, empty where unavailable."""

    resident = resident_mb()

    return {} if resident is None else {"rss_mb": resident}
