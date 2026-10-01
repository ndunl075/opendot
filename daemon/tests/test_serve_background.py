from __future__ import annotations

import sys
from pathlib import Path

import pytest

from opendot_core import always_on
from opendot_core.api import serve_cli
from opendot_core.cli import build_parser
from opendot_core.secret_store import SecretStoreError


def test_serve_accepts_log_file_and_no_background() -> None:
    args = build_parser().parse_args(["serve", "--log-file", "x.log", "--no-background"])
    assert args.log_file == "x.log" and args.no_background is True
    assert build_parser().parse_args(["serve"]).no_background is False


def test_redirect_output_appends_to_the_log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    log = tmp_path / "logs" / "daemon.log"
    serve_cli.redirect_output(str(log))
    print("hello from the service")
    sys.stdout.flush()
    assert "hello from the service" in log.read_text(encoding="utf-8")


def test_unconfigured_connectors_are_skipped_quietly() -> None:
    def needs_credentials() -> None:
        raise SecretStoreError("google-oauth-client-id")

    always_on.quiet_when_unconfigured(needs_credentials)()  # does not raise

    def real_failure() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        always_on.quiet_when_unconfigured(real_failure)()


def test_default_runner_has_no_telegram_and_syncs_in_the_background(tmp_path: Path) -> None:
    from opendot_core.db import Database

    with always_on.default_runner(Database(tmp_path / "s.db")) as runner:
        assert runner.telegram_transport is None and not runner.telegram_pairs
        assert runner.background_connectors is True
        assert runner.connectors  # calendar, gmail, github and the derived steps
