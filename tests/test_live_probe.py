"""Offline tests for tools/live_probe.py (NO network).

The .env loader/missing-keys/masking surface, the argparse surface (six
commands + write-op subparsers), the write guard, and one op's body
building against a recording fake write host. The wire paths themselves
are pinned by the loopback suite.
"""

from __future__ import annotations

import argparse
import base64
from datetime import UTC, datetime, timedelta
import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from smartbox.protocol import NodeRef, TransportUnsupportedError, WriteApi

from tests.fakes import make_jwt
from tools.live_probe import (
    _parse_bool,
    _parse_day,
    _parse_node,
    _print_json,
    _print_token_facts,
    _PrintingWriteHost,
    _probe_command,
    _run_write_op,
    build_config,
    build_parser,
    jwt_payload,
    load_env,
    main,
    mask,
    missing_keys,
)

if TYPE_CHECKING:
    from pathlib import Path


class RecordingWriteHost:
    """WriteHost fake that records posts; canned setup/prog reads."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, object]] = []
        self.status: dict[str, object] = {}
        self.setup: dict[str, object] = {}
        self.prog: dict[str, object] = {}

    async def post(self, path: str, body: object) -> None:
        self.posts.append((path, body))

    async def get_node_status(
        self,
        _dev_id: str,
        _node: NodeRef,
    ) -> dict[str, object]:
        return self.status

    async def get_node_setup(
        self,
        _dev_id: str,
        _node: NodeRef,
    ) -> dict[str, object]:
        return self.setup

    async def get_node_prog(
        self,
        _dev_id: str,
        _node: NodeRef,
    ) -> dict[str, object]:
        return self.prog


def _load_env_text(tmp_path: Path, text: str) -> dict[str, str]:
    path = tmp_path / ".env"
    path.write_text(text)
    return load_env(path)


class TestEnvLoader:
    def test_parses_key_value_with_comments_and_quotes(self, tmp_path: Path) -> None:
        env = _load_env_text(
            tmp_path,
            "# comment\n"
            "SMARTBOX_API_NAME=api\n"
            "SMARTBOX_USERNAME='user@example.com'\n"
            'SMARTBOX_PASSWORD="secret"\n'
            "\n"
            "not-a-pair\n",
        )
        assert env == {
            "SMARTBOX_API_NAME": "api",
            "SMARTBOX_USERNAME": "user@example.com",
            "SMARTBOX_PASSWORD": "secret",
        }

    def test_missing_keys_lists_absent_and_blank(self, tmp_path: Path) -> None:
        env = _load_env_text(tmp_path, "SMARTBOX_API_NAME=api\nSMARTBOX_PASSWORD=   \n")
        assert missing_keys(env) == ["SMARTBOX_USERNAME", "SMARTBOX_PASSWORD"]

    def test_build_config_supplies_basic_auth_from_reseller_table(
        self, tmp_path: Path
    ) -> None:
        env = _load_env_text(
            tmp_path,
            "SMARTBOX_API_NAME=api\n"
            "SMARTBOX_USERNAME=u@example.com\n"
            "SMARTBOX_PASSWORD=pw\n",
        )
        reseller, config = build_config(env)
        assert reseller.name == "Helki"
        assert config.api_host == "https://users-api.helki.com"
        assert config.basic_auth_creds  # from the table, not the .env
        assert config.serial_id == "1"
        assert config.referer == reseller.web_url or None


class TestMasking:
    def test_mask_keeps_short_prefix(self) -> None:
        assert mask("abcdefgh") == "abcd…"

    def test_mask_short_value(self) -> None:
        assert mask("ab") == "…"

    def test_jwt_payload_decodes(self) -> None:
        claims = {"userId": "user1"}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
        token = f"header.{payload}.signature"
        assert jwt_payload(token) == claims


class TestArgSurface:
    def test_reads_parses_selection(self) -> None:
        args = build_parser().parse_args(
            [
                "reads",
                "--dev",
                "1",
                "--node",
                "htr:2",
                "--samples-from",
                "100",
                "--samples-to",
                "200",
            ]
        )
        assert args.command == "reads"
        assert args.dev == "1"
        assert args.node == NodeRef("htr", "2")
        assert args.samples_from == 100
        assert args.samples_to == 200

    def test_ws_defaults(self) -> None:
        args = build_parser().parse_args(["ws"])
        assert (args.duration, args.heartbeat) == (60.0, 60.0)

    def test_write_ops_parse_values(self) -> None:
        parser = build_parser()
        mode = parser.parse_args(
            ["write", "--dev", "1", "--node", "htr:2", "set_mode", "manual"]
        )
        assert (mode.op, mode.mode, mode.i_know) == ("set_mode", "manual", False)
        setpoint = parser.parse_args(
            [
                "write",
                "--dev",
                "1",
                "--node",
                "htr:2",
                "set_setpoint",
                "21.5",
                "--units",
                "F",
            ]
        )
        assert (setpoint.temp, setpoint.units) == (21.5, "F")
        boost_mode = parser.parse_args(
            [
                "write",
                "--dev",
                "1",
                "--node",
                "htr:2",
                "set_boost_mode",
                "true",
                "--mode",
                "temp_and_time",
                "--temp",
                "21.5",
                "--time",
                "120",
            ]
        )
        assert boost_mode.active is True
        assert boost_mode.mode == "temp_and_time"
        assert (boost_mode.temp, boost_mode.time_minutes) == (21.5, 120)
        limit = parser.parse_args(
            [
                "write",
                "--dev",
                "1",
                "--node",
                "htr:2",
                "set_power_limit",
                "3000",
            ]
        )
        assert limit.value == 3000
        prog = parser.parse_args(
            [
                "write",
                "--dev",
                "1",
                "--node",
                "htr:2",
                "set_prog",
                "--day",
                "0=1,2,3",
                "--day",
                "1=4",
            ]
        )
        assert prog.day == [("0", [1, 2, 3]), ("1", [4])]

    def test_node_ref_type_rejects_bare_addr(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_node("htr2")


class TestWriteGuard:
    def test_write_refuses_without_guard_flag(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "write",
                "--dev",
                "1",
                "--node",
                "htr:2",
                "set_mode",
                "manual",
            ]
        )
        assert code == 2
        assert "REFUSED" in capsys.readouterr().out

    def test_write_refusal_names_the_guard_flag(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["write", "--dev", "1", "--node", "htr:2", "set_mode", "manual"])
        assert "--i-know-this-writes-to-my-heater" in capsys.readouterr().out


class TestBodyBuilding:
    async def test_set_lock_body_via_write_api(self) -> None:
        host = RecordingWriteHost()
        api = WriteApi(host)
        args = build_parser().parse_args(
            [
                "write",
                "--dev",
                "1",
                "--node",
                "htr:2",
                "set_lock",
                "true",
            ]
        )
        await _run_write_op(api, args)
        assert host.posts == [("devs/1/htr/2/status", {"locked": True})]

    async def test_device_level_op_ignores_the_node_and_names_it(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A device-level op with ``--node`` prints the ignored node.

        It used to format ``node.type`` (the attribute is ``node_type``), so
        the tool raised ``AttributeError`` before any POST - the bug that
        would have been caught by ``mypy`` on ``tools/``.
        """
        host = RecordingWriteHost()
        args = build_parser().parse_args(
            [
                "write",
                "--dev",
                "1",
                "--node",
                "htr:2",
                "set_away",
                "true",
            ]
        )
        await _run_write_op(WriteApi(host), args)
        assert "ignoring --node htr:2" in capsys.readouterr().out
        assert host.posts == [
            (
                "devs/1/mgr/away_status",
                {"away": True, "enabled": True, "forced": False},
            )
        ]

    async def test_set_mode_validates_before_any_post(self) -> None:
        host = RecordingWriteHost()
        api = WriteApi(host)
        args = build_parser().parse_args(
            [
                "write",
                "--dev",
                "1",
                "--node",
                "htr:2",
                "set_mode",
                "not-a-mode",
            ]
        )
        with pytest.raises(ValueError, match="mode must be one of"):
            await _run_write_op(api, args)
        assert host.posts == []


class TestParsers:
    def test_parse_bool(self) -> None:
        assert _parse_bool("true") is True
        assert _parse_bool("False") is False

    def test_parse_bool_rejects_other(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_bool("yes")

    def test_parse_day_rejects_bad_values(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_day("0=a,b")
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_day("0")


class TestOutputHygiene:
    """The tool's output is pasted into issues: no token, no raw device id."""

    def test_token_facts_never_print_the_token(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Presence, length and expiry only.

        The line used to read ``access token: eyJh… (never printed)``: a prefix
        of a JWT is the same for every token, so it looked like a leak while
        carrying no information.
        """
        token = make_jwt({"userId": "user-1", "exp": int(time.time()) + 100})
        bundle = SimpleNamespace(
            access_token=token,
            expires_at=datetime.now(UTC) + timedelta(seconds=100),
        )
        _print_token_facts(bundle)

        out = capsys.readouterr().out
        assert token not in out
        assert token.split(".")[1] not in out  # the secret-bearing segment
        assert "present (length" in out
        assert "never printed" not in out
        # the account-scoped claim is masked, not printed
        assert "user-1" not in out
        assert mask("user-1") in out

    def test_print_json_masks_the_node_uid(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The raw version payload carries a device identifier; mask it."""
        _print_json({"uid": "abcdef123456", "pid": "081c"})
        out = capsys.readouterr().out
        assert "abcdef123456" not in out
        assert "abcd…" in out
        assert '"pid": "081c"' in out

    async def test_write_host_masks_the_device_id_but_posts_it(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The POST line masks the device id; the request keeps it."""
        client = AsyncMock()
        host = _PrintingWriteHost(client, "device-1")

        await host.post("devs/device-1/htr/2/status", {"mode": "manual"})

        out = capsys.readouterr().out
        assert "device-1" not in out
        assert mask("device-1") in out
        client.post.assert_awaited_once_with(
            "devs/device-1/htr/2/status", {"mode": "manual"}
        )


def test_write_subparser_sleep_flag_defaults_zero():
    """--sleep spaces consecutive writes; defaults to no pause."""
    # --sleep belongs to the write subparser: it must appear BEFORE the
    # op name (the op subparser consumes everything after it).
    args = build_parser().parse_args(
        [
            "write",
            "--dev",
            "d1",
            "--node",
            "htr:2",
            "--sleep",
            "45",
            "--i-know-this-writes-to-my-heater",
            "set_lock",
            "true",
        ]
    )
    assert args.sleep == 45
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "write",
                "--dev",
                "d1",
                "--i-know-this-writes-to-my-heater",
                "set_lock",
                "true",
                "--sleep",
                "45",
            ]
        )


def test_parser_accepts_a_token_cache_path(tmp_path):
    path = str(tmp_path / "probe.json")
    for argv in (
        ["--token-cache", path, "reads"],
        ["reads", "--token-cache", path],
    ):
        args = build_parser().parse_args(argv)
        assert args.token_cache == path


async def test_probe_retries_a_seeded_token_once():
    auth = SimpleNamespace(
        get_token=AsyncMock(
            side_effect=[
                SimpleNamespace(access_token="cached"),
                SimpleNamespace(access_token="fresh"),
            ]
        ),
        invalidate=AsyncMock(),
    )
    stack = SimpleNamespace(
        config=SimpleNamespace(api_host="https://api.example.test"),
        session=object(),
        auth=auth,
    )
    probe = AsyncMock(side_effect=[TransportUnsupportedError("401"), None])

    with patch("tools.live_probe.check_ws_user_support", probe):
        assert await _probe_command(stack, seeded=True) == 0

    auth.invalidate.assert_awaited_once_with("cached")
    assert probe.await_count == 2


async def test_probe_does_not_retry_a_fresh_grant():
    """A cache the library discarded is not a seeded retry opportunity."""
    auth = SimpleNamespace(
        get_token=AsyncMock(return_value=SimpleNamespace(access_token="fresh")),
        invalidate=AsyncMock(),
    )
    stack = SimpleNamespace(
        config=SimpleNamespace(api_host="https://api.example.test"),
        session=object(),
        auth=auth,
    )
    probe = AsyncMock(side_effect=TransportUnsupportedError("401"))

    with patch("tools.live_probe.check_ws_user_support", probe):
        assert await _probe_command(stack, seeded=False) == 1

    auth.invalidate.assert_not_awaited()
    assert probe.await_count == 1
