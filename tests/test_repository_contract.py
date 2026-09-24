"""Repository completeness contract tests."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
CHANGELOG = ROOT / "CHANGELOG.md"

_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _project_version() -> str:
    match = _VERSION_RE.search(PYPROJECT.read_text(encoding="utf-8"))
    assert match, "pyproject.toml must declare [project].version"
    return match.group(1)


def test_required_repository_files_exist():
    required = [
        "README.md",
        "README.zh-CN.md",
        "CHANGELOG.md",
        "LICENSE",
        "pyproject.toml",
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
    ]

    missing = [name for name in required if not (ROOT / name).is_file()]
    assert missing == []


def test_readmes_link_release_history_and_license():
    for path in (README, README_ZH):
        text = path.read_text(encoding="utf-8")
        assert "CHANGELOG.md" in text
        assert "https://github.com/zhuhroscar-tech/zram-doctor/releases" in text
        assert "LICENSE" in text


def test_changelog_documents_current_project_version():
    version = _project_version()
    text = CHANGELOG.read_text(encoding="utf-8")

    assert f"## v{version}" in text
    assert "## v0.2.11" in text
    assert "SPDX" in text


def test_ci_builds_downloadable_release_artifacts():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "python -m build" in ci
    assert "python -m zipapp" in ci
    assert "dist/zram-doctor.pyz" in ci
    assert "SHA256SUMS.txt" in ci
    assert "actions/upload-artifact@v4" in ci


def test_codeql_workflow_is_present_for_security_scanning():
    codeql = (ROOT / ".github/workflows/codeql.yml").read_text(encoding="utf-8")

    assert "github/codeql-action/init" in codeql
    assert "github/codeql-action/analyze" in codeql
