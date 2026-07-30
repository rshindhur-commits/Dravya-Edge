from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
import tempfile


def load_json_file(file_path, default):

    if not os.path.exists(file_path):

        return default

    try:

        with open(file_path, "r") as file:

            return json.load(file)

    except json.JSONDecodeError:

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
        backup_path = f"{file_path}.corrupt.{timestamp}"
        os.replace(file_path, backup_path)
        print(
            f"[STATE WARNING] Corrupt JSON moved to {backup_path}"
        )
        return default


def json_default(value):
    """Encode values json.dump cannot serialise on its own.

    Without this, a single pandas Timestamp anywhere in the payload made
    json.dump raise `TypeError: Object of type Timestamp is not JSON
    serializable`. Timestamps reach paper trade state through `scanner_context`,
    which is built from a DataFrame row, so `open_paper_trade()` raised and
    app/runtime/paper_automation.py recorded the candidate as
    PAPER_OPEN_FAILED -- a qualifying setup dropped with no trade and no
    subscriber alert. On 2026-07-30 that silently cost five setups, including
    NVDA at setup score 100 / RR 2.97.

    Timestamps, datetimes and dates all expose isoformat(); numpy scalars expose
    item(). Anything else degrades to str() rather than taking down a state write,
    because losing type fidelity in an audit field is always better than losing
    the trade.
    """

    if hasattr(value, "isoformat"):

        try:
            return value.isoformat()
        except Exception:
            return str(value)

    if hasattr(value, "item"):

        try:
            return value.item()
        except Exception:
            return str(value)

    return str(value)


def scrub_non_finite(value):
    """Replace NaN and infinity with None, recursively.

    json.dump emits NaN and Infinity as bare literals. Python's own json.load
    accepts them, so these files round-trip inside the app and look healthy while
    being invalid JSON to everything else -- strict parsers, jq, and Postgres
    jsonb, which rejects the document outright rather than the one field.

    2026-07-30's suggested_trade_state.json carried a literal `top_candidate: NaN`
    for exactly this reason. json_default() cannot catch it: a float NaN is
    natively serialisable, so `default` is never consulted for it.

    None is the honest encoding. NaN means "no value here", and every consumer
    already treats null that way, while NaN compares false against every
    threshold it is tested against.
    """

    if isinstance(value, float):

        if math.isnan(value) or math.isinf(value):

            return None

        return value

    if isinstance(value, dict):

        return {key: scrub_non_finite(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):

        return [scrub_non_finite(item) for item in value]

    return value


def _dump(data, file):
    """Serialise with allow_nan=False so invalid JSON cannot be written silently.

    The data is scrubbed first, so this should never trigger. If some exotic
    numeric type slips past the scrub, fall back to a permissive dump rather than
    raising: json_default() already establishes that losing fidelity in an audit
    field beats losing the write, and this function is on the path that persists
    open trades.
    """

    try:

        json.dump(scrub_non_finite(data), file, indent=4, default=json_default,
                  allow_nan=False)

    except ValueError as exc:

        print(f"[STATE WARNING] non-finite value survived scrubbing: {exc}")
        file.seek(0)
        file.truncate()
        json.dump(data, file, indent=4, default=json_default)

    file.write("\n")


def save_json_file(file_path, data):

    final_path = Path(file_path).resolve()
    directory = final_path.parent

    if directory:

        directory.mkdir(
            parents=True,
            exist_ok=True
        )

    temp_path = None

    with tempfile.NamedTemporaryFile(
        "w",
        dir=str(directory),
        delete=False,
        encoding="utf-8"
    ) as file:

        temp_path = Path(file.name)

        _dump(data, file)

    def _write_direct():

        with open(final_path, "w", encoding="utf-8") as file:

            _dump(data, file)

    try:

        os.replace(
            temp_path,
            final_path
        )

    except FileNotFoundError:

        directory.mkdir(
            parents=True,
            exist_ok=True
        )

        _write_direct()

    except PermissionError as exc:

        try:

            _write_direct()

        except Exception as direct_exc:

            print(
                "[STATE WARNING] JSON save skipped after "
                f"replace permission error for {final_path}: "
                f"{exc}; direct write failed: {direct_exc}"
            )

    except OSError as exc:

        try:

            _write_direct()

        except Exception as direct_exc:

            print(
                "[STATE WARNING] JSON save skipped after "
                f"replace error for {final_path}: "
                f"{exc}; direct write failed: {direct_exc}"
            )

    finally:

        try:

            if temp_path and temp_path.exists():

                temp_path.unlink()

        except Exception:

            pass