"""The package version and the packaging metadata must agree (release tags depend on it)."""

import re
from pathlib import Path

from riftunpack import __version__


def test_pyproject_version_matches_package_version():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    match = re.search(r'^version = "([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    assert match, "pyproject.toml has no version"
    assert match.group(1) == __version__


def test_changelog_has_current_version():
    changelog = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in changelog


def test_readme_release_version_and_manifest_match_package():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert f"**v{__version__}**" in readme
    assert "SHA256SUMS.txt" in readme
