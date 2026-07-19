from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
import uuid


@dataclass(frozen=True)
class ScanGeneration:

    scan_id: str
    generation_id: str
    created_at: str
    schema_version: int = 1

    @staticmethod
    def new(scan_id: str):

        return ScanGeneration(
            scan_id=scan_id,
            generation_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )

    def metadata(self):

        return {
            "scan_id": self.scan_id,
            "generation": self.generation_id,
            "schema": self.schema_version,
            "created_at": self.created_at,
        }


def metadata_from_generation(generation=None, scan_id=None):

    if isinstance(generation, ScanGeneration):

        return generation.metadata()

    if isinstance(generation, dict):

        return generation

    if scan_id:

        return ScanGeneration.new(scan_id).metadata()

    return None


def envelope_payload(data, generation=None, scan_id=None):

    metadata = metadata_from_generation(generation, scan_id=scan_id)

    if not metadata:

        return data

    return {
        "metadata": metadata,
        "data": data,
    }


def atomic_write_json(path, payload):

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8"
    )
    os.replace(tmp_path, path)
    return path