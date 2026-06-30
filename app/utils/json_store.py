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
            indent=4
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

        with open(final_path, "w", encoding="utf-8") as file:

            json.dump(
                data,
                file,
                indent=4
            )
            file.write("\n")

    finally:

        try:

            if temp_path and temp_path.exists():

                temp_path.unlink()

        except Exception:

            pass