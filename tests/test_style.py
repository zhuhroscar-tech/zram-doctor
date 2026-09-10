import subprocess

from zram_doctor.style import (
    Style,
    bool_badge,
    resolve_style,
    status_headline,
)


def test_style_disabled_is_identity():
    s = Style(False)
    assert s.bold("x") == "x"
    assert s.green("x") == "x"
    assert s.bold_red("x") == "x"


def test_style_enabled_wraps_ansi():
    s = Style(True)
    assert s.bold("x") == "\033[1mx\033[0m"
    assert s.green("x") == "\033[32mx\033[0m"


def test_resolve_style_no_color_flag_wins():
    style = resolve_style(no_color_flag=True)
    assert style.enabled is False


def test_resolve_style_respects_no_color_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    style = resolve_style()
    assert style.enabled is False


def test_resolve_style_force_color_env_wins_over_non_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    class FakeStream:
        def isatty(self):
            return False

    style = resolve_style(stream=FakeStream())
    assert style.enabled is True


def test_resolve_style_defaults_to_tty_check(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    class TtyStream:
        def isatty(self):
            return True

    class PipeStream:
        def isatty(self):
            return False

    assert resolve_style(stream=TtyStream()).enabled is True
    assert resolve_style(stream=PipeStream()).enabled is False


def test_resolve_style_handles_stream_without_isatty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    class WeirdStream:
        pass

    assert resolve_style(stream=WeirdStream()).enabled is False


def test_bool_badge_true_false_none():
    s = Style(False)
    assert bool_badge(s, True) == "yes"
    assert bool_badge(s, False) == "no"
    assert bool_badge(s, None) == "unknown"


def test_status_headline_disabled_uses_ascii_glyph():
    s = Style(False)
    assert status_headline(s, "ok", "Healthy") == "[OK] Healthy"
    assert status_headline(s, "fail", "Blocked") == "[X] Blocked"
    assert status_headline(s, "warn", "Careful") == "[!] Careful"


def test_status_headline_enabled_uses_color_and_dot():
    s = Style(True)
    result = status_headline(s, "ok", "Healthy")
    assert "\u25cf" in result
    assert "Healthy" in result
    assert "\033[1;32m" in result


def test_status_headline_supports_info_level():
    s = Style(False)
    assert status_headline(s, "info", "Neutral note") == "[i] Neutral note"
    s2 = Style(True)
    assert "\033[2m" in status_headline(s2, "info", "Neutral note")
