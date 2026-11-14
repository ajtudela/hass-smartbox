# Live wire fixtures

Captured payloads from a real Smartbox API host, used by the passthrough tests in `tests/test_client.py` to pin the exact wire shapes the client must hand through unchanged.

They travel with this repo so the suite runs whole everywhere; CI has no access to the real hosts.

Contents: payload shapes only (setup/status dicts, numeric strings). No credentials, tokens, serial numbers or device identifiers.
