"""Regression tests for current setuptools project metadata.

Setuptools 77+ accepts SPDX license strings and explicit license-files.
Keeping the deprecated license table/classifier form causes build warnings and
future breakage, so assert the pyproject contract directly.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _pyproject_text() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


def test_project_license_uses_spdx_string_with_license_files():
    text = _pyproject_text()

    assert 'license = "MIT"' in text
    assert 'license-files = ["LICENSE"]' in text
    assert 'license = { text = "MIT" }' not in text


def test_deprecated_mit_license_classifier_is_absent():
    text = _pyproject_text()

    assert '"License :: OSI Approved :: MIT License"' not in text


def test_build_backend_requires_setuptools_with_spdx_license_support():
    text = _pyproject_text()

    assert 'requires = ["setuptools>=77", "wheel"]' in text
