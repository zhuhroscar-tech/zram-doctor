[![English](https://img.shields.io/badge/English-555555?style=flat)](README.md) [![简体中文](https://img.shields.io/badge/简体中文-555555?style=flat)](README.zh-CN.md)

# zram-doctor

A read-only Linux CLI that compares `zram-generator.conf` with running zram devices. Use it after a configuration edit to check whether the live state actually reflects your changes; `systemctl daemon-reload` alone does not reconfigure an existing device.

## What it checks

- Configured devices that are missing from the live system.
- Compression algorithm, literal size, and mount-point mismatches.
- Devices intended for swap that are not active in `swapon`.
- Live devices without a matching configuration section.

It reads merged configuration through `systemd-analyze cat-config`, plus device and swap state from `zramctl` and `swapon`. Reports are available as text or JSON.

## Install and run

Requires Linux, Python 3.9+, and util-linux tools `zramctl` and `swapon`. `systemd-analyze` is recommended for configuration drop-in merging. There are no third-party Python runtime dependencies.

```bash
git clone https://github.com/zhuhroscar-tech/zram-doctor.git
cd zram-doctor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
zram-doctor
zram-doctor --json
```

Standalone `.pyz` artifacts are available from [Releases](https://github.com/zhuhroscar-tech/zram-doctor/releases). Check the matching release's checksums before running downloaded artifacts.

Exit codes: `0` means no warning/failure finding, `1` means warnings, and `2` means a configured device is missing. **Also inspect `tool_error` in JSON**: an incomplete device query can return `0` in some cases, so exit status alone is not a complete health check.

## Safety and limits

The tool never restarts units, writes sysfs, or changes configuration. It performs no network requests or telemetry. Normal inspection usually does not require root.

Only literal sizes are compared; expressions such as `min(ram / 2, 4096)` are reported as unverifiable. Without working `systemd-analyze cat-config`, the fallback reads the first available primary config file and does not merge drop-ins. An unavailable or failed `swapon` query may also make swap findings incomplete.

Any suggested restart is a manual maintenance decision: recreating an active zram device can disrupt swap or mounted filesystems. Check memory headroom and workloads before changing live state.

## Preview and development

[Example output](docs/images/example-output.png) · [Demo video](docs/demo.mp4)

```bash
python -m pip install -e ".[dev]"
python -m pytest -v
```

[Implementation](src/zram_doctor/core.py) · [CI](.github/workflows/ci.yml) · [MIT license](LICENSE)
