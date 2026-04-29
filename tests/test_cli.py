from __future__ import annotations

import pytest

from ol_ce_sync.cli import build_parser


def test_parser_uses_short_command_name() -> None:
    parser = build_parser()

    assert parser.prog == "ol"


def test_pull_no_longer_accepts_autostash() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["pull", "--autostash"])
