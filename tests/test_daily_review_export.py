import io
import json
import zipfile

import pandas as pd

from app.analytics.daily_review_export import (
    EVIDENCE_STATUS_ARTIFACT,
    REVIEW_ARTIFACTS,
    build_daily_review_export,
)


def test_daily_review_export_contains_standard_artifacts(tmp_path):
    pd.DataFrame([{
        "Symbol": "NVDA",
        "Entry": "EMA_PULLBACK",
        "Candidate Direction": "CALL",
        "Action Status": "ENTER_PAPER",
        "Candidate RR": 2.0,
        "Option Quality Score": 90,
        "Option Spread %": 2,
        "Option Quote Freshness": "LIVE_QUOTE",
        "Affordable": True,
        "Final Signal": "BULLISH",
        "Scan ID": "scan-1",
    }]).to_csv(tmp_path / "scanner_output_close.csv", index=False)
    pd.DataFrame([{
        "candidate_rank": 1,
        "rule_evaluation": "RR below threshold",
        "setup": "EMA_PULLBACK",
        "direction": "CALL",
        "regime": "BULL",
        "final_r": 2.0,
        "trend_capture": 75,
    }]).to_csv(tmp_path / "candidate_evidence.csv", index=False)
    (tmp_path / "daily_engine_summary.json").write_text(
        json.dumps({"avg_v1_r": 2.0, "avg_trend_capture": 75}),
        encoding="utf-8",
    )
    (tmp_path / EVIDENCE_STATUS_ARTIFACT).write_text(
        json.dumps({"evidence_rows": 1, "database_status": "PERSISTED"}),
        encoding="utf-8",
    )

    archive_bytes, manifest = build_daily_review_export("2026-07-27", directory=tmp_path)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert set(REVIEW_ARTIFACTS).issubset(archive.namelist())
        assert EVIDENCE_STATUS_ARTIFACT in archive.namelist()
        assert "manifest.json" in archive.namelist()
        assert "NVDA" in archive.read("decision_waterfall.csv").decode()
    assert manifest["artifacts"]["candidate_evidence.csv"]["rows"] == 1
    assert manifest["artifacts"][EVIDENCE_STATUS_ARTIFACT]["available"]