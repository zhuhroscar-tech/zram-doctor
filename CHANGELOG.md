# Changelog

## v0.2.13 — 2026-09-25

- Made release-tag CI coverage explicit for `v*` tags so published releases rerun the same package and `.pyz` smoke checks as `main`.
- Added changelog project metadata for package installers and tightened repository-contract coverage for release-tag CI wiring and changelog links.

## v0.2.12 — 2026-09-24

- Added release-history documentation so users can see the maintenance and bug-fix timeline without opening GitHub Releases.
- Added repository contract tests covering required project files, README release links, CI wiring, CodeQL coverage, and current changelog/version parity.

## v0.2.11 — 2026-09-24

- Modernized packaging license metadata to the current SPDX string form.
- Declared `LICENSE` as an explicit license file and raised the `setuptools` build requirement to a version that supports the metadata.
- Added regression coverage so deprecated license metadata does not return.

## v0.2.10 — 2026-09-20

- Fixed `zram-size` literal parsing to match `zram-generator` semantics.
- Prevented false size-drift warnings for devices that correctly match their configured literal size.
- Verified the fix against upstream `zram-generator`/`fasteval` behavior and Linux CI.

## v0.2.9 — 2026-09-20

- Fixed `run_zramctl` crashes on syntactically valid JSON with the wrong top-level shape.
- Skipped non-object rows in otherwise valid `zramctl --json` output instead of aborting the whole scan.
- Added regression coverage for both malformed-shape cases.

## v0.2.8 — 2026-09-19

- Fixed false algorithm-drift warnings for configured algorithm strings with tuning parameters or recompression tiers.
- Compared the bare primary algorithm reported by `zramctl` with the configured primary algorithm.
- Fixed stale runtime version metadata that had drifted behind the package version.

## v0.2.7 — 2026-09-18

- Fixed a false all-clear when a zram device configured for swap was mounted as a filesystem instead.
- Added regression coverage to ensure swap-vs-mount-point drift is reported clearly.
