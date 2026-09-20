import json
import subprocess

from zram_doctor.core import (
    ConfiguredDevice,
    LiveDevice,
    _human_bytes,
    collect_and_evaluate,
    evaluate,
    get_merged_config_text,
    parse_size_literal,
    parse_zram_generator_conf,
    primary_algorithm_name,
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

    devices, missing = run_zramctl(runner=fake_runner)
    assert len(devices) == 1
    assert devices[0].name == "zram0"
    assert devices[0].disksize_bytes == 4294967296
    assert devices[0].algorithm == "lzo-rle"
    assert missing is False


def test_run_zramctl_missing_binary_returns_empty(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: None)
    devices, missing = run_zramctl()
    assert devices == []
    assert missing is True


def test_run_zramctl_handles_valid_json_non_dict_shape(monkeypatch):
    """Regression: zramctl --json is expected to emit a top-level object
    ({"zramdevices": [...]}), but run_zramctl previously called
    data.get(...) unconditionally after json.loads() succeeded --
    any syntactically-valid JSON of the wrong top-level type (e.g. a
    bare list) crashed with AttributeError: 'list' object has no
    attribute 'get' instead of degrading like the JSONDecodeError case
    just above it does. This must return the same "genuinely don't
    know" signal ([], False) as any other unparseable-output case, not
    propagate an unhandled crash out of the polling loop.
    """
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    devices, missing = run_zramctl(runner=fake_runner)
    assert devices == []
    assert missing is False


def test_run_zramctl_skips_non_dict_rows_in_valid_shape(monkeypatch):
    """A well-shaped top-level object whose row list contains a
    non-dict entry (e.g. a stray null/string) must skip that entry
    rather than crashing on row.get(...), while still returning the
    well-formed sibling rows.
    """
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"zramdevices": [None, {"name": "/dev/zram0", "disksize": "4294967296"}]}),
            stderr="",
        )

    devices, missing = run_zramctl(runner=fake_runner)
    assert missing is False
    assert len(devices) == 1
    assert devices[0].name == "zram0"


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


# --- regression: zramctl missing must not report a false "no drift" all-clear ---


def test_evaluate_zramctl_missing_does_not_claim_no_drift():
    """Before this fix, evaluate([], [], set()) with no config and no live
    devices always emitted a plain "no zram configuration or active zram
    devices found" info finding -- identical output whether the host
    genuinely has no zram, or zramctl is simply not installed and we never
    actually checked. That silently misreported an unknown state as a
    verified all-clear."""
    report = evaluate([], [], set(), zramctl_missing=True)
    assert report.tool_error is True
    assert report.has_warnings
    assert not any("no zram configuration" in f.message.lower() for f in report.findings)
    assert any("may be incomplete" in f.message.lower() for f in report.findings)


def test_evaluate_no_zram_and_zramctl_present_is_still_a_clean_info_result():
    report = evaluate([], [], set(), zramctl_missing=False)
    assert report.tool_error is False
    assert not report.has_warnings
    assert not report.has_failures
    assert any("no zram configuration" in f.message.lower() for f in report.findings)


def test_evaluate_configured_device_with_zramctl_missing_is_unknown_not_fail():
    """Before this fix: if zramctl is missing (zramctl_missing=True) but the
    host DOES have a [zramN] section in zram-generator.conf, evaluate()
    still iterated configured devices, found no matching live device (since
    live is necessarily [] when zramctl_missing), and emitted a hard "fail"
    claiming the device was never created / the systemd unit may have
    failed -- even though the real reason is simply that we never checked
    because zramctl isn't installed. That turns an "unknown" state into a
    false, actionable-looking failure. It should instead report the
    per-device state as unverifiable (no fail), and the overall tool_error
    warning still applies."""
    configured = [ConfiguredDevice(name="zram0", zram_size_expr="4096", compression_algorithm="zstd")]
    report = evaluate(configured, [], set(), zramctl_missing=True)
    assert report.tool_error is True
    assert not report.has_failures
    assert not any("no live" in f.message.lower() and "device exists" in f.message.lower() for f in report.findings)
    assert any("zramctl" in f.message.lower() for f in report.findings)


def test_run_zramctl_permission_error_does_not_produce_false_fail_end_to_end(monkeypatch):
    """Regression, end-to-end through the real run_zramctl -> evaluate path:
    a device IS configured in zram-generator.conf, but the caller lacks
    permission to query live zram state (zramctl exits nonzero). Before the
    fix, run_zramctl silently reported missing=False for this case, which
    made evaluate() treat "we never actually checked" as "we checked and it
    genuinely isn't there", emitting a hard fail telling the user their
    systemd unit may have failed to run -- when the real, fixable problem is
    just insufficient privilege to query zramctl. This must instead surface
    as the honest "could not be checked" info/warning path, with no false
    fail."""
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="zramctl: cannot open /dev/zram0: Permission denied")

    devices, zramctl_missing = run_zramctl(runner=fake_runner)
    configured = [ConfiguredDevice(name="zram0", zram_size_expr="4096", compression_algorithm="zstd")]
    report = evaluate(configured, devices, set(), zramctl_missing=zramctl_missing)

    assert report.tool_error is True
    assert not report.has_failures, (
        "a zramctl permission/runtime error must never be reported as a "
        "confirmed 'device does not exist / unit failed' fail"
    )
    assert any("zramctl" in f.message.lower() and "not available" in f.message.lower() for f in report.findings)


def test_collect_and_evaluate_propagates_zramctl_missing(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.get_merged_config_text", lambda runner=None: None)
    monkeypatch.setattr("zram_doctor.core.run_zramctl", lambda runner=None: ([], True))
    monkeypatch.setattr("zram_doctor.core.run_swapon", lambda runner=None: set())
    report = collect_and_evaluate()
    assert report.tool_error is True
    assert report.has_warnings


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


# --- regression: zram-size unit semantics must match zram-generator's REAL
# two-stage fasteval-then-MB-multiply grammar, not binary KiB/MiB/GiB ---
# Independently verified against upstream source this run:
#   - fasteval's number parser (src/parser.rs `read_const`) recognizes only
#     a single trailing ASCII letter as an SI/decimal exponent suffix
#     (k/K=1e3, M=1e6, G=1e9, T=1e12), NOT two/three-letter binary units
#     ("GiB"/"MiB" are not valid fasteval tokens at all).
#   - zram-generator's own process_size() (src/config.rs) then ALWAYS
#     multiplies whatever fasteval evaluated by 1024*1024, because the
#     whole zram-size grammar (including its documented default of
#     `min(ram / 2, 4096)`) is defined in megabytes.
# Before this fix, parse_size_literal("8G") returned 8*1024**3 (~8.6GB,
# treating G as a binary GiB unit) when the real zram-generator-computed
# value for that same config line is 8e9 MB in bytes (~8.4e21, i.e.
# zram-generator's own "units may be wrong" warning territory) -- six
# orders of magnitude apart. That mismatch made the size-drift check
# compare the live device's real (enormous, but config-faithful) byte
# count against a wrong, six-orders-smaller expectation, producing a false
# "restart the unit to fix drift" warning for a device that in fact
# exactly matches its own (poorly chosen) configuration.
def test_parse_size_literal_plain_no_suffix_is_megabytes():
    # No suffix: the literal is a plain MB count -- this case was already
    # correct before the fix (no suffix path is unaffected by the bug).
    assert parse_size_literal("4096") == 4096 * 1024 * 1024


def test_parse_size_literal_decimal_fraction_no_suffix():
    assert parse_size_literal("0.5") == int(0.5 * 1024 * 1024)


def test_parse_size_literal_si_suffix_is_decimal_not_binary():
    # G means SI-decimal 1e9 (per fasteval), applied to the MB value,
    # THEN the whole result is re-interpreted as megabytes and multiplied
    # by 1024*1024 -- NOT "8 GiB of bytes" as this function previously,
    # incorrectly, assumed.
    assert parse_size_literal("8G") == int(8 * 1_000_000_000 * 1024 * 1024)
    assert parse_size_literal("512K") == int(512 * 1_000 * 1024 * 1024)
    assert parse_size_literal("2M") == int(2 * 1_000_000 * 1024 * 1024)


def test_parse_size_literal_scientific_notation():
    # fasteval's parser also accepts bare scientific notation (e.g. "1e3"),
    # distinct from its single-letter SI suffixes.
    assert parse_size_literal("1e3") == int(1000.0 * 1024 * 1024)


def test_parse_size_literal_rejects_binary_unit_suffix():
    # "GiB"/"MiB"/"KiB" are NOT valid zram-generator/fasteval syntax --
    # a real config using them would be a parse error upstream, not a
    # valid binary-unit literal. Must be treated as "cannot verify", not
    # silently misparsed as if the trailing "iB" were ignorable noise.
    assert parse_size_literal("8GiB") is None
    assert parse_size_literal("512KiB") is None


def test_parse_size_literal_returns_none_for_expression():
    # ram/swap-relative expressions are intentionally not evaluated.
    assert parse_size_literal("min(ram / 2, 4096)") is None
    assert parse_size_literal("ram / 4") is None


def test_parse_size_literal_returns_none_for_empty():
    assert parse_size_literal("") is None
    assert parse_size_literal(None) is None


def test_parse_size_literal_rejects_negative_value():
    # A syntactically-matching negative literal is not a valid size.
    assert parse_size_literal("-5") is None


def test_evaluate_no_false_drift_when_live_device_matches_real_si_semantics():
    """Before this fix: a device configured with 'zram-size = 8G' (which
    zram-generator actually creates at 8e9 MB, per its real fasteval-then-
    MB-multiply grammar -- independently verified against upstream source)
    was compared against this tool's old, wrong, binary-GiB-based
    expectation (8*1024**3 bytes), producing a false drift warning even
    though the live device exactly matches its own configuration."""
    real_bytes = int(8 * 1_000_000_000 * 1024 * 1024)
    configured = [ConfiguredDevice(name="zram0", zram_size_expr="8G")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=real_bytes)]
    report = evaluate(configured, live, {"zram0"})
    assert not report.has_warnings
    assert not report.has_failures


def test_evaluate_flags_size_drift_for_plain_literal():
    configured = [ConfiguredDevice(name="zram0", zram_size_expr="8G")]
    # A live size that does NOT match the real (SI-then-MB) interpretation
    # of "8G" is genuine drift.
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


# --- regression: compression-algorithm= parenthesized params / recompression tiers ---
# Real upstream config syntax per `man zram-generator.conf` /
# systemd/zram-generator's own conf.md: compression-algorithm= takes "a
# whitespace-separated list string, representing the algorithms to use, and
# parameters in parentheses" -- e.g. "zstd(level=19)" for tuning, or
# "zstd lz4" where later entries are recompression tiers. zramctl only ever
# reports the single bare active algorithm name with no params/tiers, so a
# raw string comparison against the full configured value produced a false
# drift warning for every correctly-configured device using either feature.


def test_primary_algorithm_name_strips_parenthesized_params():
    assert primary_algorithm_name("zstd(level=19)") == "zstd"


def test_primary_algorithm_name_takes_first_token_of_multi_algo_list():
    assert primary_algorithm_name("zstd lz4") == "zstd"


def test_primary_algorithm_name_plain_name_unchanged():
    assert primary_algorithm_name("lzo-rle") == "lzo-rle"


def test_primary_algorithm_name_empty_is_none():
    assert primary_algorithm_name("") is None
    assert primary_algorithm_name(None) is None


def test_evaluate_no_false_drift_for_parenthesized_algorithm_params():
    """Before this fix: a device configured with tuning parameters like
    'zstd(level=19)' was compared as a raw string against zramctl's bare
    'zstd' report, always mismatching and producing a false restart-needed
    warning even when the device is running exactly the configured
    algorithm."""
    configured = [ConfiguredDevice(name="zram0", compression_algorithm="zstd(level=19)")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=4 * 1024**3)]
    report = evaluate(configured, live, {"zram0"})
    assert not report.has_warnings
    assert not any("compression-algorithm" in f.message for f in report.findings)


def test_evaluate_no_false_drift_for_multi_algorithm_recompression_list():
    """Before this fix: a device configured with a recompression tier list
    like 'zstd lz4' (zstd primary, lz4 as a later recompression tier) was
    compared as a raw string against zramctl's bare 'zstd', always
    mismatching -- even though the primary/active algorithm genuinely
    matches."""
    configured = [ConfiguredDevice(name="zram0", compression_algorithm="zstd lz4")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=4 * 1024**3)]
    report = evaluate(configured, live, {"zram0"})
    assert not report.has_warnings
    assert not any("compression-algorithm" in f.message for f in report.findings)


def test_evaluate_still_flags_real_algorithm_drift_with_params():
    """The fix must not blind the check entirely: a genuine mismatch on the
    primary algorithm (ignoring params) is still real drift."""
    configured = [ConfiguredDevice(name="zram0", compression_algorithm="lz4(level=1)")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=4 * 1024**3)]
    report = evaluate(configured, live, {"zram0"})
    assert report.has_warnings
    assert any("compression-algorithm" in f.message for f in report.findings)


def test_evaluate_flags_mount_point_drift():
    configured = [ConfiguredDevice(name="zram0", mount_point="/var/compressed")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=1024**3, mountpoint="/tmp/other")]
    report = evaluate(configured, live, {"zram0"})
    assert report.has_warnings
    assert any("mount-point" in f.message for f in report.findings)


def test_evaluate_no_mount_point_drift_when_matching():
    configured = [ConfiguredDevice(name="zram0", mount_point="/var/compressed")]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=1024**3, mountpoint="/var/compressed")]
    report = evaluate(configured, live, {"zram0"})
    assert not report.has_warnings


def test_evaluate_flags_swap_device_actually_mounted_as_filesystem():
    """Config says zram0 has no mount-point (implying swap use), but the
    live device is actually mounted as a filesystem instead. This is real
    drift -- the swap capacity the admin configured doesn't exist, and an
    unexpected filesystem mount exists instead -- but the pre-fix evaluate()
    only checked `not live_dev.mountpoint` in this branch, so a device WITH
    a mountpoint fell through every check and was reported as agreeing."""
    configured = [ConfiguredDevice(name="zram0", mount_point=None)]
    live = [LiveDevice(name="zram0", algorithm="zstd", disksize_bytes=1024**3, mountpoint="/mnt/oops")]
    report = evaluate(configured, live, swap_names=set())
    assert report.has_warnings
    assert any(
        "mounted" in f.message and "/mnt/oops" in f.message
        for f in report.findings
    )


# --- get_merged_config_text: OSError/exception paths ---


def test_get_merged_config_text_systemd_analyze_oserror_falls_back(tmp_path, monkeypatch):
    def raising_runner(cmd, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/systemd-analyze")
    fake_path = tmp_path / "zram-generator.conf"
    fake_path.write_text("[zram0]\ncompression-algorithm=lz4\n")
    monkeypatch.setattr("zram_doctor.core.CONFIG_SEARCH_PATHS", [fake_path])
    text = get_merged_config_text(runner=raising_runner)
    assert text is not None
    assert "lz4" in text


def test_get_merged_config_text_systemd_analyze_subprocess_error_falls_back(tmp_path, monkeypatch):
    def raising_runner(cmd, **kwargs):
        raise subprocess.SubprocessError("boom")

    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/systemd-analyze")
    fake_path = tmp_path / "zram-generator.conf"
    fake_path.write_text("[zram0]\ncompression-algorithm=zstd\n")
    monkeypatch.setattr("zram_doctor.core.CONFIG_SEARCH_PATHS", [fake_path])
    text = get_merged_config_text(runner=raising_runner)
    assert text is not None
    assert "zstd" in text


def test_get_merged_config_text_skips_unreadable_file_and_tries_next(tmp_path, monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: None)
    unreadable = tmp_path / "unreadable.conf"
    unreadable.write_text("[zram0]\ncompression-algorithm=lz4\n")
    unreadable.chmod(0o000)
    readable = tmp_path / "readable.conf"
    readable.write_text("[zram0]\ncompression-algorithm=zstd\n")
    monkeypatch.setattr("zram_doctor.core.CONFIG_SEARCH_PATHS", [unreadable, readable])
    try:
        text = get_merged_config_text()
    finally:
        unreadable.chmod(0o644)
    assert text is not None
    assert "zstd" in text


def test_parse_zram_generator_conf_ignores_kv_before_any_section():
    # A key=value line before any [zramN] header has no "current" device to
    # attach to and must be skipped, not raise.
    text = "compression-algorithm=zstd\n[zram0]\ncompression-algorithm=lz4\n"
    devices = parse_zram_generator_conf(text)
    assert len(devices) == 1
    assert devices[0].compression_algorithm == "lz4"


def test_parse_zram_generator_conf_ignores_malformed_kv_line():
    text = "[zram0]\nthis is not a key value line\ncompression-algorithm=zstd\n"
    devices = parse_zram_generator_conf(text)
    assert len(devices) == 1
    assert devices[0].compression_algorithm == "zstd"


# --- run_zramctl: exception/error paths ---


def test_run_zramctl_oserror_returns_empty(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")

    def raising_runner(cmd, **kwargs):
        raise OSError("boom")

    devices, missing = run_zramctl(runner=raising_runner)
    assert devices == []
    assert missing is True


def test_run_zramctl_subprocess_error_returns_empty(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")

    def raising_runner(cmd, **kwargs):
        raise subprocess.SubprocessError("boom")

    devices, missing = run_zramctl(runner=raising_runner)
    assert devices == []
    assert missing is True


def test_run_zramctl_nonzero_returncode_is_reported_as_unverifiable(monkeypatch):
    """A nonzero exit (e.g. permission denied reading /sys/block/zramN) means
    zramctl never successfully told us the live state -- it must be treated
    the same as "zramctl missing" (missing=True), not conflated with the
    legitimate "ran fine, zero devices" case (valid JSON, returncode 0),
    which is covered separately by test_run_zramctl_parses_json's sibling
    zero-device case below. Before this fix, a permission error and a
    genuine empty fleet were indistinguishable to callers."""
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="permission denied")

    devices, missing = run_zramctl(runner=fake_runner)
    assert devices == []
    assert missing is True


def test_run_zramctl_invalid_json_returns_empty(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="{not json", stderr="")

    devices, missing = run_zramctl(runner=fake_runner)
    assert devices == []
    assert missing is False


def test_run_zramctl_bad_disksize_becomes_none(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")
    payload = json.dumps(
        {"zramdevices": [{"name": "/dev/zram0", "disksize": "not-a-number", "algorithm": "zstd"}]}
    )

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    devices, missing = run_zramctl(runner=fake_runner)
    assert len(devices) == 1
    assert devices[0].disksize_bytes is None


def test_run_zramctl_falls_back_to_blockdevices_key(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/bin/zramctl")
    payload = json.dumps(
        {"blockdevices": [{"name": "zram1", "disksize": "1024", "algorithm": "lz4"}]}
    )

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    devices, missing = run_zramctl(runner=fake_runner)
    assert len(devices) == 1
    assert devices[0].name == "zram1"


# --- run_swapon: missing binary / exception / error paths ---


def test_run_swapon_missing_binary_returns_empty(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: None)
    assert run_swapon() == set()


def test_run_swapon_oserror_returns_empty(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/sbin/swapon")

    def raising_runner(cmd, **kwargs):
        raise OSError("boom")

    assert run_swapon(runner=raising_runner) == set()


def test_run_swapon_subprocess_error_returns_empty(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/sbin/swapon")

    def raising_runner(cmd, **kwargs):
        raise subprocess.SubprocessError("boom")

    assert run_swapon(runner=raising_runner) == set()


def test_run_swapon_nonzero_returncode_returns_empty(monkeypatch):
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: "/usr/sbin/swapon")

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="denied")

    assert run_swapon(runner=fake_runner) == set()


# --- _human_bytes ---


def test_human_bytes_none_is_unknown():
    assert _human_bytes(None) == "unknown"


def test_human_bytes_petabyte_scale():
    huge = 2 * 1024**5  # 2 PiB
    assert _human_bytes(huge) == "2.0PiB"


def test_human_bytes_plain_bytes_no_decimal():
    assert _human_bytes(512) == "512B"


# --- parse_size_literal: unrecognized unit ---


def test_parse_size_literal_returns_none_for_unrecognized_text():
    assert parse_size_literal("not a size at all!!") is None


# --- collect_and_evaluate: end-to-end wiring ---


def test_collect_and_evaluate_end_to_end(monkeypatch):
    def fake_runner(cmd, **kwargs):
        if cmd[0] == "/usr/bin/systemd-analyze":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="[zram0]\ncompression-algorithm=zstd\n", stderr=""
            )
        if cmd[0] == "/usr/bin/zramctl":
            return subprocess.CompletedProcess(cmd, 0, stdout=SAMPLE_ZRAMCTL_JSON, stderr="")
        if cmd[0] == "/usr/sbin/swapon":
            return subprocess.CompletedProcess(cmd, 0, stdout="/dev/zram0\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    def fake_which(name):
        return {
            "systemd-analyze": "/usr/bin/systemd-analyze",
            "zramctl": "/usr/bin/zramctl",
            "swapon": "/usr/sbin/swapon",
        }.get(name)

    monkeypatch.setattr("zram_doctor.core.shutil.which", fake_which)
    report = collect_and_evaluate(runner=fake_runner)
    assert report.configured[0].name == "zram0"
    assert report.live[0].name == "zram0"
    # config says zstd, live sample uses lzo-rle -> drift warning expected
    assert report.has_warnings


def test_collect_and_evaluate_no_config_no_devices(monkeypatch):
    """With no config search paths and no binaries available at all (including
    zramctl), the correct answer is 'we could not check' (tool_error/warn),
    not a false all-clear -- this was the exact bug this fix closes."""
    monkeypatch.setattr("zram_doctor.core.shutil.which", lambda name: None)
    monkeypatch.setattr("zram_doctor.core.CONFIG_SEARCH_PATHS", [])
    report = collect_and_evaluate(runner=subprocess.run)
    assert not report.has_failures
    assert report.has_warnings
    assert report.tool_error is True
    assert any("may be incomplete" in f.message.lower() for f in report.findings)
