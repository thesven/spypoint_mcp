import os

import pytest

from spypoint_mcp.cli import configure
from spypoint_mcp.config import Settings, SpypointError


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in os.environ:
        if key.startswith("SPYPOINT_"):
            monkeypatch.delenv(key)


def test_configure_roundtrip_special_characters_private_file(tmp_path, monkeypatch, capsys):
    secret = "a'b\\c$${HOME} !\"z"
    monkeypatch.setattr("builtins.input", lambda _: "user@example.test")
    monkeypatch.setattr("getpass.getpass", lambda _: secret)
    path = tmp_path / ".env"
    configure(path)
    settings = Settings.load(path)
    assert settings.password == secret
    assert settings.username == "user@example.test"
    assert path.stat().st_mode & 0o777 == 0o600
    assert settings.download_dir == tmp_path / "downloads"
    assert secret not in capsys.readouterr().out
    assert secret not in repr(settings)
    with pytest.raises(SpypointError, match="already exists"):
        configure(path)


def test_environment_overrides_explicit_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("SPYPOINT_USERNAME=file\nSPYPOINT_PASSWORD=local\n")
    monkeypatch.setenv("SPYPOINT_USERNAME", "environment")
    assert Settings.load(path).username == "environment"


def test_no_implicit_env_discovery(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SPYPOINT_TOKEN=should-not-load\n")
    monkeypatch.chdir(tmp_path)
    assert Settings.load().token == ""


@pytest.mark.parametrize(
    "name,value",
    [
        ("TIMEOUT_SECONDS", "nan"),
        ("TIMEOUT_SECONDS", "bad"),
        ("MAX_IMAGE_BYTES", "1"),
        ("INCLUDE_SHARED", "yes"),
    ],
)
def test_invalid_config_rejected(monkeypatch, name, value):
    monkeypatch.setenv("SPYPOINT_" + name, value)
    with pytest.raises(SpypointError):
        Settings.load()
