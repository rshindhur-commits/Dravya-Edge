from __future__ import annotations

from html import escape

import streamlit as st


TONES = {"ok", "warn", "bad", "neutral"}


def kpi_card(label: str, value: str, help_text: str | None = None):

    safe_label = escape(str(label))
    safe_value = escape(str(value))
    title = escape(str(help_text or value))

    st.markdown(
        f"""
        <div class="metric-card" title="{title}">
            <div class="metric-label">{safe_label}</div>
            <div class="metric-value">{safe_value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_card_grid(cards):
    """Compact status cards with an explicitly stated tone.

    `_render_compact_card_grid` infers its tone from the value text, which works
    for vocabulary like OK/FAILED but cannot know that "990m ago" is bad. An
    operator console needs the caller to decide, so tone is passed in.

    cards: iterable of (label, value, tone) with tone in ok / warn / bad / neutral.
    """

    parts = ['<div class="compact-grid">']

    for label, value, tone in cards:
        tone = tone if tone in TONES else "neutral"
        parts.append(
            '<div class="compact-card compact-{tone}">'
            '<div class="compact-label">{label}</div>'
            '<div class="compact-value" title="{value}">{value}</div>'
            '</div>'.format(
                tone=tone,
                label=escape(str(label)),
                value=escape(str(value)),
            )
        )

    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def operator_bar(title: str, pills):
    """Page identity plus the one-glance mode indicators.

    pills: iterable of (text, tone) with tone in live / post / bad / neutral.
    """

    rendered = "".join(
        '<span class="op-pill op-pill-{tone}">{text}</span>'.format(
            tone=escape(str(tone)), text=escape(str(text))
        )
        for text, tone in pills
    )
    # The pill group needs a class of its own: an unclassed div is a flex item
    # that cannot shrink past its widest nowrap pill, which is what pushed the
    # bar off the side of a phone screen.
    st.markdown(
        f'<div class="op-bar"><div class="op-bar-title">{escape(str(title))}</div>'
        f'<div class="op-pills">{rendered}</div></div>',
        unsafe_allow_html=True,
    )


def _r_gauge(r_multiple):
    """A -1R to +3R track with the zero line marked.

    Position risk is only readable against the stop, so the gauge is anchored on
    -1R rather than on zero: half a bar means the trade is at breakeven.
    """

    low, high = -1.0, 3.0
    span = high - low
    zero = (0.0 - low) / span * 100.0

    if r_multiple is None:
        return (
            f'<div class="r-track"><div class="r-zero" style="left:{zero:.1f}%"></div></div>'
        )

    clamped = max(low, min(high, float(r_multiple)))
    point = (clamped - low) / span * 100.0
    colour = "#22c55e" if clamped >= 0 else "#ef4444"
    left, width = (zero, point - zero) if clamped >= 0 else (point, zero - point)

    return (
        '<div class="r-track">'
        f'<div class="r-fill" style="left:{left:.1f}%;width:{max(width, 0.8):.1f}%;'
        f'background:{colour}"></div>'
        f'<div class="r-zero" style="left:{zero:.1f}%"></div>'
        '</div>'
    )


def position_card(symbol, subtitle, r_multiple, fields, tone="neutral"):
    """One open position, sized so three fit above the fold.

    fields: iterable of (label, value) rendered as a compact figure grid.
    """

    tone = tone if tone in TONES else "neutral"
    r_text = "-" if r_multiple is None else f"{float(r_multiple):+.2f}R"
    r_colour = (
        "inherit" if r_multiple is None
        else "#16a34a" if float(r_multiple) >= 0 else "#dc2626"
    )

    figures = "".join(
        '<div><div class="pos-field">{label}</div>'
        '<div class="pos-figure">{value}</div></div>'.format(
            label=escape(str(label)),
            value=escape("-" if value in (None, "") else str(value)),
        )
        for label, value in fields
    )

    st.markdown(
        f'<div class="pos-card compact-{tone}">'
        f'<div class="pos-head">'
        f'<div><div class="pos-symbol">{escape(str(symbol))}</div>'
        f'<div class="pos-sub">{escape(str(subtitle))}</div></div>'
        f'<div class="pos-r" style="color:{r_colour}">{escape(r_text)}</div>'
        f'</div>'
        f'{_r_gauge(r_multiple)}'
        f'<div class="pos-grid">{figures}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
