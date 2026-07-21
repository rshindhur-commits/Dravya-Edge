from __future__ import annotations

from dataclasses import asdict, dataclass

from app.gates.rule_evaluation import build_rule_evaluations


@dataclass(frozen=True)
class EntrySnapshot:
    trade_id: str
    scan_id: str | None
    trading_day: str | None
    symbol: str | None
    direction: str | None
    setup: str | None
    action: str | None
    score: object
    setup_score: object
    rr: object
    option_quality: object
    option_spread: object
    option_delta: object
    option_dte: object
    option_cost: object
    market_regime: object
    breadth: object
    sector_strength: object
    qqq_strength: object
    spy_strength: object
    vix: object
    ema9: object
    ema20: object
    vwap: object
    atr: object
    rel_volume: object
    macd: object
    rsi: object
    trend_health: object
    candidate_rank: object
    persistence_count: object
    score_delta: object
    first_seen: object
    entered_at: object
    entry_price: object
    stop: object
    target: object
    ideal_entry: object
    distance_from_ema: object
    distance_from_vwap: object
    chase_pct: object
    rule_evaluations: list[dict]
    diagnostics_json: object

    def to_record(self):
        return asdict(self)


def _get(context, *names):
    for name in names:
        value = (context or {}).get(name)
        if value is not None and str(value).lower() not in {"", "nan", "none"}:
            return value
    return None


def create_entry_snapshot(trade):
    trade = trade or {}
    context = trade.get("scanner_context") or {}
    scan_id = trade.get("scan_id") or _get(context, "scan_id", "Scan ID")
    entry_price = trade.get("entry_price")
    ema9 = _get(context, "EMA9")
    vwap = _get(context, "VWAP")

    def distance(reference):
        try:
            return round(float(entry_price) - float(reference), 4)
        except Exception:
            return None

    def percent(reference):
        try:
            return round((float(entry_price) - float(reference)) / float(reference) * 100, 3)
        except Exception:
            return None
    return EntrySnapshot(
        trade_id=trade.get("trade_id") or trade.get("trade_key"), scan_id=scan_id,
        trading_day=trade.get("trading_day"), symbol=trade.get("symbol"), direction=trade.get("direction"),
        setup=trade.get("entry_type") or _get(context, "Entry"), action=_get(context, "Action Status"),
        score=_get(context, "15m Score", "score"), setup_score=_get(context, "Setup %"),
        rr=trade.get("planned_rr") or _get(context, "Candidate RR", "RR"), option_quality=_get(context, "Option Quality Score"),
        option_spread=_get(context, "Option Spread %"), option_delta=_get(context, "Option Delta"), option_dte=_get(context, "DTE", "Option DTE"), option_cost=_get(context, "Option Contract Cost"),
        market_regime=_get(context, "Market Regime"), breadth=_get(context, "Watchlist Breadth Score"), sector_strength=_get(context, "Sector Strength"),
        qqq_strength=_get(context, "RS vs QQQ", "QQQ Strength"), spy_strength=_get(context, "RS vs SPY", "SPY Strength"), vix=_get(context, "VIX"),
        ema9=_get(context, "EMA9"), ema20=_get(context, "EMA20"), vwap=_get(context, "VWAP"), atr=_get(context, "ATR"),
        rel_volume=_get(context, "Relative Volume"), macd=_get(context, "MACD"), rsi=_get(context, "RSI"), trend_health=_get(context, "Trend Health State"),
        candidate_rank=_get(context, "Candidate Rank"), persistence_count=_get(context, "Candidate Persistence"), score_delta=_get(context, "Candidate Score Delta"),
        first_seen=_get(context, "First Seen At"), entered_at=trade.get("opened_at_et") or trade.get("opened_at"), entry_price=trade.get("entry_price"),
        stop=trade.get("stop_loss"), target=trade.get("take_profit"),
        ideal_entry=ema9 or entry_price,
        distance_from_ema=distance(ema9), distance_from_vwap=distance(vwap), chase_pct=percent(ema9),
        rule_evaluations=[item.to_record() for item in build_rule_evaluations(context, scan_id)] if scan_id else [],
        diagnostics_json=_get(context, "ENTRY_DIAGNOSTICS_JSON"),
    )
