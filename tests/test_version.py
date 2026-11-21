import json
from pathlib import Path
import re
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


def test_smartbox_library_pins_agree():
    """Every install path must require the same library version."""
    root = Path(__file__).resolve().parents[1]
    texts = [
        (root / "custom_components/smartbox/manifest.json").read_text(encoding="utf-8"),
        (root / "pyproject.toml").read_text(encoding="utf-8"),
        (root / "README.md").read_text(encoding="utf-8"),
        (root / "CONTRIBUTING.md").read_text(encoding="utf-8"),
    ]
    specs = {
        match
        for text in texts
        for match in re.findall(r"smartbox(>=[0-9.]+(?:,<[0-9.]+)?)", text)
    }
    assert specs == {">=3.2.1,<3.3"}, specs
