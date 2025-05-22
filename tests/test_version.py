import re

import custom_components.smartbox


def test_version():
    assert re.match(
        r"^[0-9\.]+(-[a-z0-9\.]+)?$", custom_components.smartbox.__version__
    )
