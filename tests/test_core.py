import json
import subprocess

from zram_doctor.core import (
    ConfiguredDevice,
    LiveDevice,
    evaluate,
    get_merged_config_text,
    parse_size_literal,
    parse_zram_generator_conf,
    run_swapon,
    run_zramctl,
)

SAMPLE_CONF = """
[zram0]
zram-size = min(ram / 2, 4096)
compression-algorithm = zstd

[zram1]
mount-point = /var/compressed
compression-algorithm = lz4
"""


def test_parse_zram_generator_conf_extracts_sections():
    devices = parse_zram_generator_conf(SAMPLE_CONF)
    assert len(devices) == 2
    zram0 = next(d for d in devices if d.name == "zram0")
    assert zram0.compression_algorithm == "zstd"
    assert zram0.zram_size_expr == "min(ram / 2, 4096)"
    zram1 = next(d for d in devices if d.name == "zram1")
    assert zram1.mount_point == "/var/compressed"


def test_parse_zram_generator_conf_ignores_comments_and_blank_lines():
    text = "# comment\n\n[zram0]\n; also a comment\ncompression-algorithm = zstd\n"
    devices = parse_zram_generator_conf(text)
    assert devices[0].compression_algorithm == "zstd"


def test_parse_zram_generator_conf_empty_text():
    assert parse_zram_generator_conf("") == []


def test_get_merged_config_text_prefers_systemd_analyze(tmp_path, monkeypatch):
    def fake_runner(cmd, **kwargs):
        if "systemd-analyze" in cmd[0] or cmd[0] == "systemd-analyze":
            return subprocess.CompletedProcess(cmd, 0, stdout="[zram0]\ncompression-algorithm=zstd\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/systemd-analyze")
    text = get_merged_config_text(runner=fake_runner)
    assert "zstd" in text


def test_get_merged_config_text_falls_back_to_files(tmp_path, monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: None)
    fake_path = tmp_path / "zram-generator.conf"
    fake_path.write_text("[zram0]\ncompression-algorithm=lz4\n")
    monkeypatch.setattr("zram_doctor.core.CONFIG_SEARCH_PATHS", [fake_path])
    text = get_merged_config_text()
    assert "lz4" in text


def test_get_merged_config_text_returns_none_if_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: None)
    monkeypatch.setattr("zram_doctor.core.CONFIG_SEARCH_PATHS", [tmp_path / "nope.conf"])
    assert get_merged_config_text() is None


SAMPLE_ZRAMCTL_JSON = json.dumps(
    {
        "zramdevices": [
            {"name": "/dev/zram0", "disksize": "4294967296", "algorithm": "lzo-rle", "mountpoint": None},
        ]
    }
)


def test_run_zramctl_parses_json(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=SAMPLE_ZRAMCTL_JSON, stderr="")

    devices = run_zramctl(runner=fake_runner)
    assert len(devices) == 1
    assert devices[0].name == "zram0"
    assert devices[0].disksize_bytes == 4294967296
    assert devices[0].algorithm == "lzo-rle"


def test_run_zramctl_missing_binary_returns_empty(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: None)
    assert run_zramctl() == []


def test_run_swapon_parses_names(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/sbin/swapon")

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="/dev/zram0\n/dev/sda2\n", stderr="")

    assert run_swapon(runner=fake_runner) == {"zram0", "sda2"}


def test_evaluate_flags_missing_live_device():
    configured = [ConfiguredDevice(name="zram0", compression_algorithm="zstd")]
    report = evaluate(configured, [], set())
    assert report.has_failures
    assert any("no live" in f.message for f in report.findings)


def test_evaluate_flags_algorithm_drift():
    configured = [ConfiguredDevice(name="zram0", compression_algorithm="zstd")]
    live = [LiveDevice(name="zram0", algorithm="lzo-rle", disksize_bytes=4 * 1024**3)]
    report = evaluate(configured, live, {"zram0"})
    assert report.has_warnings
    assert any("compression-algorithm" in f.message for f in report.findings)


def test_evaluate_no_drift_when_matching():
    configured = [ConfiguredDevice(name="zram0", compression_algorithm="zstd")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=4 * 1024**3)]
    report = evaluate(configured, live, {"zram0"})
    assert not report.has_failures
    assert not report.has_warnings
    assert any("no drift" in f.message.lower() for f in report.findings)


def test_evaluate_no_config_no_devices_is_informational():
    report = evaluate([], [], set())
    assert not report.has_failures
    assert not report.has_warnings
    assert any("no zram configuration" in f.message.lower() for f in report.findings)


def test_evaluate_flags_unconfigured_live_device_as_info():
    live = [LiveDevice(name="zram5", algorithm="lz4", disksize_bytes=1024**3)]
    report = evaluate([], live, set())
    assert not report.has_failures
    assert any("zram5" in f.message and f.level == "info" for f in report.findings)


def test_evaluate_flags_configured_swap_device_not_in_swapon():
    configured = [ConfiguredDevice(name="zram0")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=1024**3)]
    report = evaluate(configured, live, set())
    assert report.has_warnings
    assert any("swapon" in f.message for f in report.findings)


def test_parse_size_literal_plain_mib_default():
    assert parse_size_literal("4096") == 4096 * 1024 * 1024


def test_parse_size_literal_gib_suffix():
    assert parse_size_literal("8G") == 8 * 1024**3
    assert parse_size_literal("8GiB") == 8 * 1024**3


def test_parse_size_literal_kib_suffix():
    assert parse_size_literal("512K") == 512 * 1024


def test_parse_size_literal_returns_none_for_expression():
    # ram/swap-relative expressions are intentionally not evaluated.
    assert parse_size_literal("min(ram / 2, 4096)") is None
    assert parse_size_literal("ram / 4") is None


def test_parse_size_literal_returns_none_for_empty():
    assert parse_size_literal("") is None
    assert parse_size_literal(None) is None


def test_evaluate_flags_size_drift_for_plain_literal():
    configured = [ConfiguredDevice(name="zram0", zram_size_expr="8G")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=4 * 1024**3)]
    report = evaluate(configured, live, {"zram0"})
    assert report.has_warnings
    assert any("zram-size" in f.message and "8G" in f.message for f in report.findings)


def test_evaluate_no_size_drift_when_literal_matches():
    configured = [ConfiguredDevice(name="zram0", zram_size_expr="4096")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=4096 * 1024 * 1024)]
    report = evaluate(configured, live, {"zram0"})
    assert not report.has_warnings
    assert not report.has_failures


def test_evaluate_reports_info_for_unverifiable_size_expression():
    configured = [ConfiguredDevice(name="zram0", zram_size_expr="min(ram / 2, 4096)")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=4 * 1024**3)]
    report = evaluate(configured, live, {"zram0"})
    assert not report.has_warnings
    assert not report.has_failures
    assert any(
        "ram/swap-relative expression" in f.message and f.level == "info"
        for f in report.findings
    )
