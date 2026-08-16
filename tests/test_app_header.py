"""The header has to span the page and paint no background of its own.

Both things that made the previous header read as a small card in the corner
were properties of the markup rather than of the artwork: an image ends where
its pixels end, and one carrying its own plate draws that plate over the page.
A test that only checks the header renders would have passed throughout, so
these check the two properties directly.
"""

import base64

import pytest

from app import dashboard


class _Recorder:
    """Stands in for streamlit, keeping whatever the header wrote."""

    def __init__(self):
        self.markdown_calls = []
        self.titles = []
        self.captions = []

    def markdown(self, body, **_kwargs):
        self.markdown_calls.append(body)

    def title(self, text):
        self.titles.append(text)

    def caption(self, text):
        self.captions.append(text)

    @property
    def html(self):
        return "\n".join(self.markdown_calls)


@pytest.fixture
def rendered(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(dashboard, "st", recorder)
    dashboard.render_app_header()
    return recorder


def test_the_header_stretches_instead_of_ending_with_the_artwork(rendered):
    """The gold rule absorbs the leftover width.

    That is what makes this a page header rather than a card: it reaches the
    container edge whatever the wordmark happens to measure.
    """

    assert 'class="de-rule"' in rendered.html
    assert "flex:1 1 auto" in rendered.html
    assert "width:100%" in rendered.html


def test_the_header_paints_no_background_of_its_own(rendered):
    """A plate of its own is what drew the visible box over the page."""

    block = rendered.html.split(".de-header {", 1)[1].split("}", 1)[0]

    assert "background" not in block


def test_the_mark_is_an_inline_transparent_svg(rendered):
    assert "data:image/svg+xml;base64," in rendered.html

    payload = rendered.html.split("data:image/svg+xml;base64,", 1)[1].split('"', 1)[0]

    assert b"<svg" in base64.b64decode(payload)


def test_the_wordmark_is_text_not_pixels(rendered):
    """It has to survive a failed image load and reach a screen reader."""

    assert "DRAVYA <b>EDGE</b>" in rendered.html


def test_it_degrades_rather_than_wraps_on_a_narrow_viewport(rendered):
    """Under 760px the second half of the tagline hides instead of wrapping."""

    assert "@media (max-width:760px)" in rendered.html
    assert 'class="de-long"' in rendered.html


def test_the_top_margin_answers_the_trimmed_page_padding(monkeypatch):
    """These two numbers are a pair.

    `.block-container` is trimmed to 1.3rem, under the ~3.75rem Streamlit's
    fixed toolbar occupies, and the header buys the difference back on itself so
    no other page shifts. If the trim is ever reverted, this margin stops being
    a correction and becomes a double gap.
    """

    recorder = _Recorder()
    monkeypatch.setattr(dashboard, "st", recorder)

    dashboard._inject_compact_dashboard_css()
    dashboard.render_app_header()

    assert "padding-top: 1.3rem" in recorder.html
    assert "margin:2rem" in recorder.html


def test_a_missing_mark_falls_back_to_readable_text(monkeypatch, tmp_path):
    """The fallback restates both lines, because the header carries no heading."""

    recorder = _Recorder()
    monkeypatch.setattr(dashboard, "st", recorder)
    monkeypatch.setattr(dashboard, "ROOT_DIR", tmp_path)

    dashboard.render_app_header()

    assert recorder.titles == ["Dravya Edge"]
    assert recorder.captions and "Directional signals" in recorder.captions[0]
    assert not recorder.markdown_calls
