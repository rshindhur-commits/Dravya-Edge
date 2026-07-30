from __future__ import annotations

import json
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

        json.dump(
            data,
            file,
            indent=4,
            default=json_default
        )
        file.write("\n")

    def _write_direct():

        with open(final_path, "w", encoding="utf-8") as file:

            json.dump(
                data,
                file,
                indent=4,
                default=json_default
            )
            file.write("\n")

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