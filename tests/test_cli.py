import json

from zram_doctor.cli import main
from zram_doctor.core import Finding, Report


def _fake_report(has_failures=False, has_warnings=False, tool_error=False):
    findings = [Finding("info", "all good")]
    if has_warnings:
        findings.append(Finding("warn", "drift"))
    if has_failures:
        findings.append(Finding("fail", "missing device"))
    return Report(configured=[], live=[], findings=findings, tool_error=tool_error)


def test_cli_no_drift_returns_0(monkeypatch, capsys):
    monkeypatch.setattr("zram_doctor.cli.collect_and_evaluate", lambda: _fake_report())
    rc = main([])
    assert rc == 0
    assert "no drift detected" in capsys.readouterr().out.lower()


def test_cli_warnings_return_1(monkeypatch, capsys):
    monkeypatch.setattr(
        "zram_doctor.cli.collect_and_evaluate", lambda: _fake_report(has_warnings=True)
    )
    rc = main([])
    assert rc == 1


def test_cli_failures_return_2(monkeypatch, capsys):
    monkeypatch.setattr(
        "zram_doctor.cli.collect_and_evaluate",
        lambda: _fake_report(has_failures=True, has_warnings=True),
    )
    rc = main([])
    assert rc == 2


def test_cli_json_output(monkeypatch, capsys):
    monkeypatch.setattr("zram_doctor.cli.collect_and_evaluate", lambda: _fake_report())
    rc = main(["--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["has_failures"] is False


def test_cli_tool_error_shows_incomplete_headline_not_ok(monkeypatch, capsys):
    """Regression: a tool_error report with no other findings must not print
    the plain 'no drift detected' all-clear headline."""
    monkeypatch.setattr(
        "zram_doctor.cli.collect_and_evaluate",
        lambda: _fake_report(has_warnings=True, tool_error=True),
    )
    rc = main([])
    out = capsys.readouterr().out.lower()
    assert rc == 1
    assert "may be incomplete" in out
    assert "no drift detected" not in out


def test_cli_json_output_includes_tool_error(monkeypatch, capsys):
    monkeypatch.setattr(
        "zram_doctor.cli.collect_and_evaluate",
        lambda: _fake_report(has_warnings=True, tool_error=True),
    )
    rc = main(["--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["tool_error"] is True
