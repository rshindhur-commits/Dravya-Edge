"""Pinning the theme decouples it from the reader's operating system.

`.streamlit/config.toml` fixes the app to brand navy for everyone. The CSS media
query `prefers-color-scheme` still follows the reader's OS, so the two stopped
being the same signal the moment the theme was pinned. A rule that branches on
the media query will therefore disagree with the page around it -- a reader on a
light OS taking the light branch while the page stays dark.

That is invisible to anyone whose OS matches, which is why it needs a test
rather than an eye.
"""

import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".streamlit" / "config.toml"


def test_the_theme_is_pinned_to_brand_navy():
    theme = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["theme"]

    assert theme["base"] == "dark"
    assert theme["backgroundColor"].upper() == "#0A1A2F"


def test_the_config_ships_rather_than_being_ignored():
    """.gitignore excludes .streamlit/secrets.toml only. If that ever widens to
    the directory, the theme stops reaching production and nothing complains."""

    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    entries = {line.strip() for line in ignored if line.strip()}

    assert ".streamlit/secrets.toml" in entries
    assert not {".streamlit", ".streamlit/", ".streamlit/*"} & entries


def test_no_injected_css_branches_on_the_readers_colour_scheme():
    """The trap this file exists for.

    Held as a source scan rather than a render check because the rule can be
    injected from any page module, and the failure only shows on a machine whose
    OS setting differs from the pinned theme.
    """

    # CSS comments are stripped first. The rule this replaced is described in a
    # comment at the site, deliberately, and a rule inside a comment is not a
    # rule -- without this the test would fire on its own explanation.
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "app").rglob("*.py")
        if "prefers-color-scheme"
        in re.sub(
            r"/\*.*?\*/",
            "",
            path.read_text(encoding="utf-8", errors="ignore"),
            flags=re.DOTALL,
        )
    ]

    assert offenders == [], (
        "the theme is pinned dark, so these must not branch on the OS scheme: "
        + ", ".join(offenders)
    )
