"""Core logic for zram-doctor.

The problem: zram-generator.conf declares desired zram devices (size,
compression algorithm, etc.), and systemd applies that configuration by
creating `/dev/zramN` devices at boot via `systemd-zram-setup@zramN.service`.
But that generator only re-reads the config file and (re)creates devices
when its systemd units run -- editing zram-generator.conf and even running
`systemctl daemon-reload` does NOT retroactively change an already-running
zram device. The device keeps its *old* size/algorithm until the unit is
explicitly restarted (which also destroys and recreates the device, briefly
losing whatever was swapped into it).

This is a well-documented, recurring source of confusion (see e.g. the
"zram Ignored My Config, and systemd Said It Worked" writeup, and
systemd/zram-generator issue #78 on applying config changes): the config
file says one size/algorithm, `systemctl status` reports success, but the
*actual* running zram0 device is still using stale settings -- and there is
no built-in command that says "these two disagree."

zram-doctor is a small, read-only diagnostic: it reads the merged
zram-generator.conf configuration (preferring `systemd-analyze cat-config`,
which correctly implements systemd's drop-in/override precedence, with a
manual fallback for systems where that's unavailable), reads the live state
of each `/dev/zramN` device via `zramctl`, and reports any device where the
configured and running size or compression algorithm disagree -- plus a few
adjacent sanity checks (configured device never created; zram device present
but not backing swap or a filesystem as configured).

This tool never writes to sysfs, never restarts units, and never modifies
any configuration. It only reads.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CONFIG_SEARCH_PATHS = [
    Path("/run/systemd/zram-generator.conf"),
    Path("/etc/systemd/zram-generator.conf"),
    Path("/usr/local/lib/systemd/zram-generator.conf"),
    Path("/usr/lib/systemd/zram-generator.conf"),
]


@dataclass
class ConfiguredDevice:
    name: str  # e.g. "zram0"
    zram_size_expr: Optional[str] = None
    compression_algorithm: Optional[str] = None
    mount_point: Optional[str] = None


@dataclass
class LiveDevice:
    name: str
    disksize_bytes: Optional[int] = None
    algorithm: Optional[str] = None
    mountpoint: Optional[str] = None
    is_swap: bool = False


@dataclass
class Finding:
    level: str  # "fail" | "warn" | "info"
    message: str


@dataclass
class Report:
    configured: list
    live: list
    findings: list = field(default_factory=list)
    tool_error: bool = False

    @property
    def has_failures(self) -> bool:
        return any(f.level == "fail" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.level == "warn" for f in self.findings)


def get_merged_config_text(runner=subprocess.run) -> Optional[str]:
    """Prefer `systemd-analyze cat-config`, which correctly implements
    systemd's drop-in precedence rules; fall back to manually reading the
    first existing file in CONFIG_SEARCH_PATHS (drop-ins are NOT merged in
    the fallback -- this is a known, documented limitation)."""
    systemd_analyze = shutil.which("systemd-analyze")
    if systemd_analyze:
        try:
            proc = runner(
                [systemd_analyze, "cat-config", "systemd/zram-generator.conf"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            proc = None
        if proc is not None and proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout

    for path in CONFIG_SEARCH_PATHS:
        try:
            if path.exists():
                return path.read_text()
        except OSError:
            continue
    return None


_SECTION_RE = re.compile(r"^\[(zram\d+)\]\s*$")
_KV_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$")


def parse_zram_generator_conf(text: str) -> list:
    """Parse zram-generator.conf's INI-like format into ConfiguredDevice list.

    Only understands [zramN] sections; the [zram-generator] global section
    (if present) is intentionally ignored since it doesn't configure a
    specific device.
    """
    devices = {}
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1)
            devices.setdefault(current, ConfiguredDevice(name=current))
            continue
        if current is None:
            continue
        m = _KV_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2)
        dev = devices[current]
        if key == "zram-size":
            dev.zram_size_expr = value
        elif key == "compression-algorithm":
            dev.compression_algorithm = value
        elif key == "mount-point":
            dev.mount_point = value
    return list(devices.values())


def run_zramctl(runner=subprocess.run) -> tuple:
    """Returns (devices, zramctl_missing) -- zramctl_missing distinguishes
    "the binary isn't installed, so we genuinely don't know" from "it ran
    and found zero devices", which matter differently to callers."""
    zramctl_bin = shutil.which("zramctl")
    if not zramctl_bin:
        return [], True
    try:
        proc = runner(
            [zramctl_bin, "--output-all", "--bytes", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return [], True
    if proc.returncode != 0 or not proc.stdout:
        return [], False
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [], False
    rows = data.get("zramdevices") or data.get("blockdevices") or []
    devices = []
    for row in rows:
        name = row.get("name", "")
        name = name.rsplit("/", 1)[-1] if name else name
        disksize = row.get("disksize")
        try:
            disksize_bytes = int(disksize) if disksize not in (None, "") else None
        except (TypeError, ValueError):
            disksize_bytes = None
        devices.append(
            LiveDevice(
                name=name,
                disksize_bytes=disksize_bytes,
                algorithm=row.get("algorithm") or None,
                mountpoint=row.get("mountpoint") or None,
            )
        )
    return devices, False


def run_swapon(runner=subprocess.run) -> set:
    swapon_bin = shutil.which("swapon")
    if not swapon_bin:
        return set()
    try:
        proc = runner(
            [swapon_bin, "--show=NAME", "--noheadings"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()
    names = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            names.add(line.rsplit("/", 1)[-1])
    return names


def _human_bytes(n: Optional[int]) -> str:
    if n is None:
        return "unknown"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024:
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}{unit}"
        value /= 1024
    return f"{value:.1f}PiB"


# zram-generator's zram-size grammar allows plain numeric literals (with an
# optional K/M/G/T suffix, MiB-based binary units, default unit is MiB when
# none given) *or* arithmetic expressions referencing "ram"/"swap" totals
# and min()/max() -- see `man zram-generator.conf`. We can only safely
# verify the plain-literal case without re-implementing that whole
# expression grammar and re-deriving the host's RAM/swap totals the same
# way the generator does; for anything else we report the drift check as
# genuinely unknown rather than silently skipping it or guessing.
_SIZE_LITERAL_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([KMGT]i?B?|[kmgt])?$")
_SIZE_UNIT_MULTIPLIERS = {
    "": 1024 * 1024,  # zram-generator's default unit is MiB
    "K": 1024,
    "KIB": 1024,
    "M": 1024 * 1024,
    "MIB": 1024 * 1024,
    "G": 1024 * 1024 * 1024,
    "GIB": 1024 * 1024 * 1024,
    "T": 1024 * 1024 * 1024 * 1024,
    "TIB": 1024 * 1024 * 1024 * 1024,
}


def parse_size_literal(expr: str) -> Optional[int]:
    """Parse a plain numeric zram-size literal (e.g. "4096", "8G", "512MiB")
    into bytes. Returns None for anything that isn't a plain literal --
    including ram/swap-relative expressions like "min(ram / 2, 4096)",
    which this intentionally does NOT attempt to evaluate (see module note
    above): callers must treat None as "cannot verify", not "zero"."""
    if not expr:
        return None
    m = _SIZE_LITERAL_RE.match(expr.strip())
    if not m:
        return None
    number, unit = m.group(1), (m.group(2) or "").upper()
    multiplier = _SIZE_UNIT_MULTIPLIERS.get(unit)
    if multiplier is None:
        return None
    return int(float(number) * multiplier)


def evaluate(configured: list, live: list, swap_names: set, zramctl_missing: bool = False) -> Report:
    findings = []
    live_by_name = {d.name: d for d in live}

    for cfg in configured:
        live_dev = live_by_name.get(cfg.name)
        if live_dev is None:
            if zramctl_missing:
                # We never actually queried live device state (zramctl is
                # missing), so we genuinely don't know whether this device
                # exists -- reporting "fail" here would misrepresent an
                # unknown state as a verified problem. Defer to the
                # tool_error warning emitted below instead.
                findings.append(
                    Finding(
                        "info",
                        f"{cfg.name} is configured in zram-generator.conf, but its live "
                        f"state could not be checked because zramctl is not available.",
                    )
                )
            else:
                findings.append(
                    Finding(
                        "fail",
                        f"{cfg.name} is configured in zram-generator.conf but no live "
                        f"/dev/{cfg.name} device exists. The generator/unit may not have "
                        f"run yet, or failed -- check `systemctl status systemd-zram-setup@{cfg.name}`.",
                    )
                )
            continue

        if cfg.compression_algorithm and live_dev.algorithm:
            if cfg.compression_algorithm.strip().lower() != live_dev.algorithm.strip().lower():
                findings.append(
                    Finding(
                        "warn",
                        f"{cfg.name}: config requests compression-algorithm="
                        f"'{cfg.compression_algorithm}' but the running device is using "
                        f"'{live_dev.algorithm}'. The device was likely created before this "
                        f"config change; restart systemd-zram-setup@{cfg.name}.service to "
                        f"apply it (this recreates the device and briefly drops its swapped data).",
                    )
                )

        if cfg.zram_size_expr and live_dev.disksize_bytes is not None:
            configured_bytes = parse_size_literal(cfg.zram_size_expr)
            if configured_bytes is None:
                findings.append(
                    Finding(
                        "info",
                        f"{cfg.name}: zram-size='{cfg.zram_size_expr}' is a ram/swap-relative "
                        f"expression (not a plain literal), so its target size cannot be "
                        f"verified against the live device (currently "
                        f"{_human_bytes(live_dev.disksize_bytes)}) without re-implementing "
                        f"zram-generator's size grammar -- compare manually if unsure.",
                    )
                )
            elif configured_bytes != live_dev.disksize_bytes:
                findings.append(
                    Finding(
                        "warn",
                        f"{cfg.name}: config requests zram-size={cfg.zram_size_expr} "
                        f"({_human_bytes(configured_bytes)}) but the running device is "
                        f"{_human_bytes(live_dev.disksize_bytes)}. The device was likely "
                        f"created before this config change; restart "
                        f"systemd-zram-setup@{cfg.name}.service to apply it (this recreates "
                        f"the device and briefly drops its swapped data).",
                    )
                )

        if cfg.mount_point:
            if not live_dev.mountpoint or Path(live_dev.mountpoint) != Path(cfg.mount_point):
                findings.append(
                    Finding(
                        "warn",
                        f"{cfg.name}: config sets mount-point={cfg.mount_point} but the "
                        f"live device reports mountpoint="
                        f"{live_dev.mountpoint or 'none'}.",
                    )
                )
        elif cfg.mount_point is None and cfg.name not in swap_names and not live_dev.mountpoint:
            findings.append(
                Finding(
                    "warn",
                    f"{cfg.name} is configured with no mount-point (implying swap use) "
                    f"but is not currently active in `swapon --show`. It may have failed "
                    f"to activate as swap, or been swapoff'd manually.",
                )
            )

    configured_names = {c.name for c in configured}
    for live_dev in live:
        if live_dev.name not in configured_names:
            findings.append(
                Finding(
                    "info",
                    f"{live_dev.name} is active but has no matching [zram...] section in "
                    f"the current zram-generator.conf (size {_human_bytes(live_dev.disksize_bytes)}). "
                    f"It may have been set up manually with zramctl, or the config was "
                    f"changed after this device was created.",
                )
            )

    if not configured and not live:
        if zramctl_missing:
            findings.append(
                Finding(
                    "warn",
                    "zramctl is not available, so live zram device state could not be "
                    "checked. This result may be incomplete -- install util-linux's "
                    "zramctl and re-run rather than treating this as 'no zram in use'.",
                )
            )
        else:
            findings.append(Finding("info", "No zram configuration or active zram devices found."))
    elif not findings:
        findings.append(
            Finding("info", "Configured and running zram devices agree -- no drift detected.")
        )

    return Report(configured=configured, live=live, findings=findings, tool_error=zramctl_missing)


def collect_and_evaluate(runner=subprocess.run) -> Report:
    config_text = get_merged_config_text(runner=runner)
    configured = parse_zram_generator_conf(config_text) if config_text else []
    live, zramctl_missing = run_zramctl(runner=runner)
    swap_names = run_swapon(runner=runner)
    return evaluate(configured, live, swap_names, zramctl_missing=zramctl_missing)
