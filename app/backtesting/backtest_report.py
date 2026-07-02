from pathlib import Path

from app.analytics.expectancy_report import build_grouped_expectancy_reports


def build_backtest_report(backtest_result):

    trades_df = backtest_result.get("trades")
    candidates_df = backtest_result.get("candidates")
    expectancy_reports = build_grouped_expectancy_reports(trades_df)

    return {
        "candidate_count": 0 if candidates_df is None else len(candidates_df),
        "trade_count": 0 if trades_df is None else len(trades_df),
        "expectancy": expectancy_reports,
    }


def write_backtest_report(backtest_result, output_path="reports/backtest_report.html"):

    report = build_backtest_report(backtest_result)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    sections = [
        "<html><head><title>Backtest Report</title></head><body>",
        "<h1>Backtest Report</h1>",
        f"<p>Candidates: {report['candidate_count']}</p>",
        f"<p>Trades: {report['trade_count']}</p>",
    ]

    for group_name, table in report["expectancy"].items():

        sections.append(f"<h2>Expectancy by {group_name}</h2>")
        sections.append(table.to_html(index=False))

    sections.append("</body></html>")
    output_file.write_text("\n".join(sections), encoding="utf-8")

    return {
        "path": str(output_file),
        "candidate_count": report["candidate_count"],
        "trade_count": report["trade_count"],
    }