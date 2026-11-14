import json
from pathlib import Path
import tomllib

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
