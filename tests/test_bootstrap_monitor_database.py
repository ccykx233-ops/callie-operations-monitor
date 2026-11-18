import importlib
import os
import sys

import pytest


def test_bootstrap_cli_requires_apply_without_reading_secrets(monkeypatch):
    module = importlib.import_module("scripts.bootstrap_monitor_database")

    class ForbiddenEnvironment(dict):
        def get(self, key, default=None):
            if key in {"DATABASE_OWNER_URL", "MONITOR_ROLE_PASSWORD"}:
                raise AssertionError("secret read before apply")
            return super().get(key, default)

    monkeypatch.setattr(os, "environ", ForbiddenEnvironment())
    monkeypatch.setattr(sys, "argv", ["bootstrap_monitor_database.py"])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2


def test_bootstrap_rejects_short_password_without_connecting(monkeypatch):
    module = importlib.import_module("scripts.bootstrap_monitor_database")
    monkeypatch.setattr(
        module.psycopg,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("connected")),
    )
    with pytest.raises(ValueError, match="BOOTSTRAP_CONFIGURATION_INVALID"):
        module.bootstrap("postgresql://invalid", "short")
