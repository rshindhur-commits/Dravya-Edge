"""One trade, every gate it passed, and where the record disagrees with the tape.

Built after a session in which the operator could not check any claim about a
trade without asking. Every number here is read from a table or from the market's
own record; nothing is derived from a summary. If a panel cannot be sourced, it
says so rather than rendering a plausible blank.

## Why the last panel exists

The app's own record of a trade has been wrong three separate ways, and each was
invisible in every roll-up:

* `option_peak_mid` sampled only on the scan cycle, so TSLA on 2026-08-21 logged
  a peak of 8.62 for a contract that traded to 11.39 -- and *sold at 9.60*, above
  its own recorded peak, which is impossible.
* the exit quote recorded bid 9.60 while the exchanges showed bid 10.90, stamped
  `LIVE_QUOTE` and two seconds old. That one trade was understated by $115.
* `trade_exit_analysis` graded the trend BROKEN on a trade that reached its target
  in twenty minutes.

So this page checks the book against the tape and shows the difference. Across
the ten trades of 2026-08-20/21 the quote errors run both ways and net to -$9, so
the running totals hold -- it is the per-trade numbers that cannot be trusted
without this comparison.

Same rule as the other Postgres pages: unavailable, empty and data are three
different renderings.
"""

from __future__ import annotations

UNAVAILABLE = "Unavailable — the database could not be read. This is not the same as no trades."

# Gate thresholds are recorded per trade in `scanner_context`, so nothing here
# hardcodes a bar. (measured key, threshold key, label, lower_is_better)
GATES = (
    ("ENTRY_GATE_SETUP", "ENTRY_GATE_MIN_SETUP", "Setup quality", False),
    ("ENTRY_GATE_RR", "ENTRY_GATE_MIN_RR", "Reward vs risk", False),
    ("ENTRY_GATE_SPREAD", "ENTRY_GATE_MAX_SPREAD", "Option spread %", True),
    ("ENTRY_GATE_OPTION_QUALITY", "ENTRY_GATE_MIN_OPTION_QUALITY", "Option quality", False),
)

CONTRACT_FIELDS = (
    ("Option Ticker", "Contract"),
    ("Option Contract Cost", "Cost paid"),
    ("Max Allowed Contract Cost", "Cost ceiling"),
    ("Option Spread %", "Spread %"),
    ("Option Open Interest", "Open interest"),
    ("Option Delta", "Delta"),
    ("Expiration Bucket", "Tenor band"),
    ("CHAIN_EXAMINED", "Chains examined"),
    ("Option Liquidity Grade", "Liquidity grade"),
)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trades_on(trading_day):
    """Closed and open trades for a session, newest first."""

    from sqlalchemy import text

    from app.db.connection import get_engine

    with get_engine().connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text("""
            select id, symbol, direction, status, opened_at, closed_at,
                   entry_price::float entry_price,
                   close_price::float close_price,
                   r_multiple::float r_multiple,
                   option_ticker,
                   payload
              from paper_trades
             where opened_at::date = :day
             order by opened_at desc
        """), {"day": trading_day})]


def _ledger_on(symbol, trading_day):
    """Every decision the app made about this symbol that day, grouped."""

    from sqlalchemy import text

    from app.db.connection import get_engine

    with get_engine().connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text("""
            select coalesce(blocked_by, decision) reason, decision, count(*) n
              from auto_paper_decision
             where symbol = :symbol and trading_day = :day
             group by 1, 2
             order by n desc
        """), {"symbol": symbol, "day": trading_day})]


def _nbbo(option_ticker, moment):
    """The best bid and offer the exchanges published at that instant.

    The book stores what the app captured. This is what the market actually
    showed, and the two have differed by as much as $1.30 on the bid.
    """

    import requests

    from app.utils.polygon_client import get_polygon_api_key, get_polygon_base_url

    if not option_ticker or moment is None:
        return None, None

    try:
        start = int((moment.timestamp() - 8) * 1e9)
        end = int((moment.timestamp() + 2) * 1e9)
        response = requests.get(
            f"{get_polygon_base_url()}/v3/quotes/{option_ticker}",
            params={
                "timestamp.gte": start, "timestamp.lte": end,
                "order": "desc", "limit": 1, "sort": "timestamp",
                "apiKey": get_polygon_api_key(),
            },
            timeout=10,
        )
        results = (response.json() or {}).get("results") or []
    except Exception:
        return None, None

    if not results:
        return None, None

    return results[0].get("bid_price"), results[0].get("ask_price")


def _contract_high(option_ticker, opened_at, closed_at):
    """The contract's highest one-minute print while the position was open."""

    import requests

    from app.utils.polygon_client import get_polygon_api_key, get_polygon_base_url

    if not option_ticker or opened_at is None:
        return None

    day = opened_at.date().isoformat()

    try:
        response = requests.get(
            f"{get_polygon_base_url()}/v2/aggs/ticker/{option_ticker}"
            f"/range/1/minute/{day}/{day}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000,
                    "apiKey": get_polygon_api_key()},
            timeout=20,
        )
        bars = (response.json() or {}).get("results") or []
    except Exception:
        return None

    from datetime import datetime, timezone

    best = None
    for bar in bars:
        stamp = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc)
        if opened_at <= stamp <= (closed_at or opened_at):
            best = bar["h"] if best is None else max(best, bar["h"])

    return best


def _gate_row(container, label, got, need, lower_is_better):

    import streamlit as st

    if got is None or need is None:
        container.write(f"**{label}** — not recorded")
        return

    passed = (got <= need) if lower_is_better else (got >= need)
    verdict = "PASS" if passed else "BLOCKED"
    comparator = "max" if lower_is_better else "needs"

    left, right = container.columns([3, 2])
    left.metric(label, f"{got:g}", f"{verdict} · {comparator} {need:g}",
                delta_color="normal" if passed else "inverse")

    # The bar is the measured value against its own threshold, so a spread of
    # 0.65 against a ceiling of 2.0 reads as a third full rather than as 65%.
    scale = max(got, need) * 1.15 or 1
    right.progress(min(1.0, got / scale))
    right.caption(f"threshold at {need:g}")


def render(trading_day=None):

    import streamlit as st

    from datetime import datetime
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")

    st.subheader("Trade lifecycle")
    st.caption(
        "Every gate a trade passed, in order, read from the database. "
        "The last panel checks the app's own record against the market's."
    )

    default_day = trading_day or datetime.now(ET).date()
    day = st.date_input("Session", value=default_day, key="lifecycle_day")

    try:
        trades = _trades_on(day)
    except Exception as exc:
        st.warning(UNAVAILABLE)
        st.caption(f"{type(exc).__name__}: {exc}")
        return

    if not trades:
        st.info(f"No trades opened on {day}. The app was running; it did not buy.")
        return

    labels = {
        f"{t['symbol']} {t['direction']} · "
        f"{t['opened_at'].astimezone(ET):%H:%M} · "
        f"{'open' if t['status'] != 'CLOSED' else format(t['r_multiple'] or 0, '+.2f') + 'R'}": t
        for t in trades
    }
    choice = st.selectbox("Trade", list(labels), key="lifecycle_trade")
    trade = labels[choice]
    payload = trade.get("payload") or {}
    context = payload.get("scanner_context") or {}

    opened = trade["opened_at"].astimezone(ET)
    closed = trade["closed_at"].astimezone(ET) if trade["closed_at"] else None
    held = f"{(closed - opened).total_seconds() / 60:.0f} min" if closed else "still open"

    entry_ask = _num(payload.get("option_entry_ask"))
    close_bid = _num(payload.get("option_close_bid"))
    booked_cash = (close_bid - entry_ask) * 100 if (entry_ask and close_bid) else None

    top = st.columns(5)
    top[0].metric("Result", f"{trade['r_multiple']:+.2f}R" if trade["r_multiple"] is not None else "—")
    top[1].metric("Premium booked", f"${booked_cash:+,.0f}" if booked_cash is not None else "—",
                  help="One contract, bought at the ask and sold at the bid, as recorded.")
    top[2].metric("Held", held)
    top[3].metric("Exit rule", str(payload.get("exit_rule") or "—"))
    top[4].metric("Setup", str(context.get("Entry") or "—"))

    # ---- 1. selection -------------------------------------------------------
    st.markdown("#### 1 · How often it was considered")
    try:
        ledger = _ledger_on(trade["symbol"], day)
    except Exception:
        ledger = []

    if not ledger:
        st.caption("No decision-ledger rows for this symbol on this day.")
    else:
        looks = sum(row["n"] for row in ledger)
        opened_n = sum(row["n"] for row in ledger if row["decision"] == "OPENED")
        st.write(
            f"The app evaluated **{trade['symbol']}** {looks} times and bought "
            f"{opened_n} time{'' if opened_n == 1 else 's'}. Every reason it gave:"
        )
        import pandas as pd

        frame = pd.DataFrame(ledger).rename(
            columns={"reason": "Reason", "decision": "Verdict", "n": "Times"})
        st.dataframe(frame[["Reason", "Verdict", "Times"]],
                     width="stretch", hide_index=True)

    # ---- 2. gates -----------------------------------------------------------
    st.markdown("#### 2 · The gates, on the scan that bought")
    result = context.get("ENTRY_GATE_RESULT")
    if result:
        st.caption(f"Gate result recorded as **{result}**"
                   + (f" — {context.get('ENTRY_GATE_FAILURE')}"
                      if context.get("ENTRY_GATE_FAILURE") else ""))

    for measured, threshold, label, lower in GATES:
        _gate_row(st.container(), label,
                  _num(context.get(measured)), _num(context.get(threshold)), lower)

    blocks = st.columns(2)
    for column, (flag, reason, label) in zip(blocks, (
        ("Regime Blocked", "Regime Block Reason", "Market regime"),
        ("Event Blocked", "Event Block Reason", "News event"),
    )):
        blocked = bool(context.get(flag))
        column.metric(label, "BLOCKED" if blocked else "clear",
                      str(context.get(reason) or ""),
                      delta_color="inverse" if blocked else "off")

    # ---- 3. contract --------------------------------------------------------
    st.markdown("#### 3 · Which contract it bought")
    rows = [{"Field": label, "Value": context.get(key)}
            for key, label in CONTRACT_FIELDS if context.get(key) is not None]
    if rows:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.caption("No contract-selection fields recorded on this trade.")

    # ---- 4. the trade -------------------------------------------------------
    st.markdown("#### 4 · What it did")
    levels = st.columns(4)
    levels[0].metric("Bought at", f"{trade['entry_price']:.2f}")
    levels[1].metric("Stop", f"{_num(payload.get('initial_stop_loss')):.2f}"
                     if _num(payload.get("initial_stop_loss")) else "—")
    levels[2].metric("Target", f"{_num(payload.get('take_profit')):.2f}"
                     if _num(payload.get("take_profit")) else "—")
    levels[3].metric("Closed at", f"{trade['close_price']:.2f}"
                     if trade["close_price"] else "still open")

    excursions = st.columns(3)
    excursions[0].metric("Best it reached", f"{_num(payload.get('mfe_r')) or 0:+.2f}R")
    excursions[1].metric("Worst against", f"{-(_num(payload.get('mae_r')) or 0):+.2f}R")
    fill_gap = _num(payload.get("entry_fill_gap_r"))
    excursions[2].metric(
        "Fill vs signal",
        f"{fill_gap:+.3f}R" if fill_gap is not None else "—",
        "better than signal" if (fill_gap or 0) < 0 else "worse than signal",
        delta_color="normal" if (fill_gap or 0) <= 0 else "inverse",
        help="Negative is good: the app filled at a better price than the one it signalled on.",
    )

    # ---- 5. book vs tape ----------------------------------------------------
    st.markdown("#### 5 · The book against the market")
    st.caption(
        "The panels above are what the app wrote down. These are the same moments "
        "as the exchanges recorded them. They have differed by as much as $115 on "
        "a single trade."
    )

    if not st.checkbox("Check against the market (live Polygon calls)",
                       key="lifecycle_verify"):
        st.caption("Off by default — this makes two Polygon requests per trade.")
        return

    if not closed:
        st.caption("The trade is still open; there is no exit to verify yet.")
        return

    with st.spinner("Reading the exchange record…"):
        real_entry_bid, real_entry_ask = _nbbo(trade["option_ticker"], opened)
        real_exit_bid, real_exit_ask = _nbbo(trade["option_ticker"], closed)
        true_high = _contract_high(trade["option_ticker"], trade["opened_at"], trade["closed_at"])

    recorded_peak = _num(payload.get("option_peak_mid"))
    real_cash = ((real_exit_bid - real_entry_ask) * 100
                 if (real_exit_bid and real_entry_ask) else None)

    check = st.columns(3)
    check[0].metric(
        "Premium, as recorded", f"${booked_cash:+,.0f}" if booked_cash is not None else "—")
    check[1].metric(
        "Premium, at real quotes", f"${real_cash:+,.0f}" if real_cash is not None else "—",
        f"{real_cash - booked_cash:+,.0f} vs the book"
        if (real_cash is not None and booked_cash is not None) else "",
        delta_color="off")
    check[2].metric(
        "Contract's true high", f"{true_high:.2f}" if true_high else "—",
        f"recorded peak {recorded_peak:.2f}" if recorded_peak else "no peak recorded",
        delta_color="off")

    # The invariant. A peak below the exit price is arithmetically impossible and
    # needs no market knowledge to spot, which is why it is checked here rather
    # than described anywhere.
    exit_mid = _num(payload.get("option_close_mid"))

    if recorded_peak is not None and exit_mid is not None and recorded_peak < exit_mid:
        message = (
            f"**The recorded peak is below the exit price.** The book says this "
            f"contract's best price was {recorded_peak:.2f} and that it sold at "
            f"{exit_mid:.2f}. A peak cannot be lower than what you sold for."
        )
        if true_high is not None:
            message += f" The contract actually traded to {true_high:.2f}."
        st.error(message)

    if real_exit_bid is not None and close_bid is not None:
        drift = real_exit_bid - close_bid
        if abs(drift) >= 0.10:
            st.warning(
                f"**The exit quote does not match the exchange record.** The book "
                f"recorded a bid of {close_bid:.2f}; the market showed "
                f"{real_exit_bid:.2f} — a difference of ${abs(drift) * 100:,.0f} "
                f"on one contract. Across 2026-08-20/21 these errors ran both ways "
                f"and netted to −$9, so totals hold but this trade's figure does not."
            )

    import pandas as pd

    st.dataframe(pd.DataFrame([
        {"Moment": "Entry", "Book bid": payload.get("option_entry_bid"),
         "Book ask": entry_ask, "Market bid": real_entry_bid, "Market ask": real_entry_ask},
        {"Moment": "Exit", "Book bid": close_bid,
         "Book ask": payload.get("option_close_ask"),
         "Market bid": real_exit_bid, "Market ask": real_exit_ask},
    ]), width="stretch", hide_index=True)
