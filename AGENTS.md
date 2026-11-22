# AGENTS.md — hass-smartbox

Home Assistant custom integration for Smartbox / Haverland heaters (domain `smartbox`). The stack is three layers: `smartbox.protocol` (wire) → `smartbox.core` (domain state) → the HA adapter. Since `3.1.0-rc.1` the first two live in the separate `smartbox` library and `manifest.json` requires `smartbox>=3.0.0` (published on PyPI); the 3.0.0 release vendored the same code. A development checkout needs the library installed. [ARCHITECTURE.md](docs/ARCHITECTURE.md) explains how it works, [CONTRIBUTING.md](CONTRIBUTING.md) is the contributor guide, and `docs/` carries the wire record.

This file is the operating manual for any agent working in this repo.

## Hard rules

- **Git.** Never run history-mutating commands (`commit`, `push`, `tag`, `merge`, `rebase`, `reset`, ...). Leave changes in the working tree; commit only on an explicit instruction ("commit this", "commit and push"). "Do X" is not authorization to commit X. Read-only git commands are fine.
- **Credentials.** Never read or print any `.env` file (no `cat`, no `grep` into it) — only its key names matter. Probe output stays masked: no tokens, basic-auth headers, passwords or token-bearing URLs.
- **Wire facts have one home: `docs/api-contract.md`.** New facts are probed with `tools/live_probe.py`, recorded there with provenance, and only then encoded; shipped text cites the section (`docs/api-contract.md §5.1`) and never restates a rule as folklore. `docs/webapi-spec.md` holds the decoded web app's statics; `docs/api-ws-user.md` the current apps' behavior.
- **Shipped text is self-contained.** Code, tests and docs state the reason for a decision, may reference repo history (versions, commits), and cite in-repo paths. They never point at agent instructions or local-only files; the fuller conventions are in [CONTRIBUTING.md](CONTRIBUTING.md).
- **Keep the docs true.** When behavior changes, update what describes it: `docs/ARCHITECTURE.md`, `README.md`, `CHANGELOG.md`, the `docs/` records.

## Environment and gates

- Python 3.14 venv at `.venv`, managed by `uv` (`uv sync --all-extras --dev`).
- `uv sync --all-extras --dev` installs the pinned `smartbox` library too; to develop against local library changes, install the sibling checkout editable: `uv pip install --python .venv/bin/python -e ../smartbox`.
- `.venv/bin/python -m pytest -q` · `.venv/bin/ruff check custom_components tests tools` · `.venv/bin/ruff format --check custom_components tests tools` · `.venv/bin/mypy custom_components tools`
- CI runs ruff and mypy directly and the test suite through tox against the newest allowed Home Assistant; a green local run can still fail CI  (details: `CONTRIBUTING.md`).

## Live wire probing

`tools/live_probe.py` drives the real hosts from the CLI for wire investigation; it imports the installed `smartbox.protocol` library. It reads the user's own gitignored `.env` at the repo root (`SMARTBOX_API_NAME`, `SMARTBOX_USERNAME`, `SMARTBOX_PASSWORD`) — never read or print it. Write commands refuse to run without `--i-know-this-writes-to-my-heater`, because they move real heaters. Command reference: `README.md` ("Development / live probing").

## Working state

- `docs/status.md` is the short live-state file (current focus, open hardware-gated items, release state). Check it before picking up work; keep it small and prune entries as they close. Completed work belongs to git history.
- Local working notes live under `notes/` (excluded from git; absent in a  clone). Nothing shipped may cite them.
