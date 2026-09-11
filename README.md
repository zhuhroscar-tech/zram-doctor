# zram-doctor

[![CI](https://github.com/zhuhroscar-tech/zram-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/zhuhroscar-tech/zram-doctor/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/zhuhroscar-tech/zram-doctor?include_prereleases&label=release)](https://github.com/zhuhroscar-tech/zram-doctor/releases)
![Linux](https://img.shields.io/badge/platform-Linux-111111?logo=linux)

Detects drift between `zram-generator.conf` and the actually-running zram
device(s) on Linux — the gap behind "I changed my config, systemd said it
worked, but the device is still using the old settings."

## Simple explanation

Checks whether your compressed-RAM swap (zram) settings actually match what
is running right now. It catches the common trap where you edit the zram
config, everything reports success, but the live device silently keeps
using the old settings until you explicitly restart it.

## The problem

`zram-generator` (the systemd unit generator that manages compressed-RAM
swap, standard on Fedora and increasingly common elsewhere) only applies
`zram-generator.conf` at the moment its `systemd-zram-setup@zramN.service`
unit runs. Editing the config file — even running `systemctl daemon-reload`
— does **not** retroactively reconfigure an already-running `/dev/zramN`
device. You have to explicitly `systemctl restart systemd-zram-setup@zramN`
(which destroys and recreates the device, briefly dropping whatever was
swapped into it) for a config change to actually apply.

This is a real, documented trap:
- [`systemd/zram-generator#78`](https://github.com/systemd/zram-generator/issues/78)
  — "Apply config changes?" — walks through exactly this confusion.
- ["zram Ignored My Config, and systemd Said It Worked"](https://dustinvk.com/blog/zram-ignored-my-config)
  — a full write-up of configuring an 8GiB zstd device, `systemctl` reporting
  success, and the live device staying at 4GiB with `lzo-rle`.
- The Raspberry Pi OS `rpi-swap` writeback confusion — `free`/`swapon`
  report the same numbers whether or not the on-disk writeback file exists,
  because they were never designed to distinguish that state.

No existing tool answers "does my config match what's actually running?" —
you have to manually cross-reference `zram-generator.conf`, `zramctl`, and
`swapon --show` yourself, every time.

## What this does

```
$ zram-doctor
Configured zram devices: zram0
Live zram devices:       zram0

[WARN] zram0: config requests compression-algorithm='zstd' but the running
       device is using 'lzo-rle'. The device was likely created before this
       config change; restart systemd-zram-setup@zram0.service to apply it
       (this recreates the device and briefly drops its swapped data).

Result: drift detected -- config and running state disagree (see WARN above).
```

It reads the merged `zram-generator.conf` (via `systemd-analyze cat-config`,
which correctly implements systemd's drop-in override precedence — with a
plain-file fallback when that's unavailable), reads the live device state
via `zramctl` and `swapon --show`, and reports:

- a configured device that never came up at all (**fail**)
- a live device whose compression algorithm or mount point disagrees with
  the config (**warn** — the fix, and the trade-off of applying it, are
  spelled out)
- a configured swap device that isn't showing up in `swapon --show` (**warn**)
- a live device with no matching config section, for awareness (**info**)

**Read-only.** zram-doctor never restarts a unit, never writes to sysfs,
never touches your configuration files. It only reads.

## Install

Requires Python 3.9+ and `zramctl` (from `util-linux`, present on virtually
every Linux system). `systemd-analyze` is used opportunistically for
correct config merging when available.

```bash
pip install --user zram-doctor   # once published to PyPI
```

Or grab the standalone `.pyz` from a GitHub Release (no pip/venv needed):

```bash
curl -LO https://github.com/zhuhroscar-tech/zram-doctor/releases/download/v0.1.0/zram-doctor.pyz
python3 zram-doctor.pyz --help
```

Or from source:

```bash
git clone https://github.com/zhuhroscar-tech/zram-doctor.git
cd zram-doctor
pip install --user .
```

## Usage

Run any time after editing `zram-generator.conf`, or whenever `free`/swap
numbers look surprising:

```bash
zram-doctor          # human-readable report
zram-doctor --json   # machine-readable
```

Exit codes: `0` no drift, `1` drift found (warnings), `2` a configured
device is completely missing.

## Uninstall

```bash
pip uninstall zram-doctor
```

No config files, no persistent state — stateless read-only check.

## Privacy & permissions

No network access, no telemetry. Reads `systemd-analyze cat-config`,
`zram-generator.conf` (plain file fallback), `zramctl`, and `swapon --show`
— all read-only, no root required for any of these on a standard install.

## Distro / architecture support

Pure Python (stdlib only). Works on any Linux distribution and architecture
where `util-linux`'s `zramctl` is present (i.e. virtually all of them).
`systemd-analyze` is optional — its absence just means config drop-ins
outside the primary config file location aren't merged (a documented
limitation, not a silent failure).

## Reproducible build & test

```bash
git clone https://github.com/zhuhroscar-tech/zram-doctor.git
cd zram-doctor
python3 -m venv .venv && . .venv/bin/activate
pip install -e . pytest
pytest -v
python -m build
python -m zipapp build/pyz-deps -m "zram_doctor.cli:main" -o dist/zram-doctor.pyz
```

CI (`.github/workflows/ci.yml`) runs the same steps on real Ubuntu Linux
GitHub Actions runners, including creating a real zram device with
`zramctl` and a real `zram-generator.conf`, then smoke-testing the
installed console script and the standalone `.pyz` against that live state.

## License

MIT — see [LICENSE](LICENSE).
