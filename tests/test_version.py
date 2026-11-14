import json
from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.version import Version

import custom_components.smartbox

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
MANIFEST = Path(custom_components.smartbox.__file__).parent / "manifest.json"


def test_version():
    """The module version is a valid PEP 440 version."""
    assert Version(custom_components.smartbox.__version__)


def test_manifest_version_matches_module():
    """The manifest and the module must carry the same version (HA reads the manifest)."""
    manifest_version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    assert manifest_version == custom_components.smartbox.__version__


def test_pyproject_version_matches_module():
    """A release bump must update the built metadata as well as the manifest."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert project["project"]["version"] == custom_components.smartbox.__version__


def test_library_pin_matches_manifest():
    """The smartbox library pin is stated twice and must not drift.

    The manifest ``requirements`` entry is what Home Assistant installs for a
    HACS user; the ``pyproject`` dependency is what pip/uv install for anyone
    building this package or syncing the development environment. Both pin
    the same tagged tarball URL: a library bump must land in both in one
    commit, or the installed runtime and the development environment
    silently diverge.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    manifest_pins = [r for r in manifest["requirements"] if r.startswith("smartbox@")]
    assert len(manifest_pins) == 1, manifest["requirements"]
    pyproject_pins = [
        str(req)
        for req in (Requirement(r) for r in project["project"]["dependencies"])
        if req.name == "smartbox"
    ]
    assert len(pyproject_pins) == 1, project["project"]["dependencies"]
    assert manifest_pins[0] == pyproject_pins[0]
