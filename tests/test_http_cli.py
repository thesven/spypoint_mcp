import json
import sys

import httpx
import pytest

from spypoint_mcp import cli
from spypoint_mcp.client import SpypointClient
from spypoint_mcp.config import Settings, SpypointError
from spypoint_mcp.server import create_server
from spypoint_mcp.transport import http_app

TOKEN = "test-only-http-token-" + "x" * 32


def rpc_response(response):
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("No JSON-RPC response in HTTP stream")


async def test_http_auth_and_stateful_mcp_roundtrip(api):
    app = http_app(create_server(client=api), TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            assert (await client.post("/mcp", json={})).status_code == 401
            response = await client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
            assert response.status_code == 401 and TOKEN not in response.text
            duplicates = [
                ("Authorization", "Bearer " + TOKEN),
                ("Authorization", "Bearer " + TOKEN),
            ]
            assert (await client.post("/mcp", json={}, headers=duplicates)).status_code == 401
            client.headers.update(
                {
                    "Authorization": "Bearer " + TOKEN,
                    "Accept": "application/json, text/event-stream",
                }
            )
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            assert response.status_code == 200
            assert rpc_response(response)["result"]["serverInfo"]["name"] == "spypoint"
            client.headers["MCP-Session-Id"] = response.headers["MCP-Session-Id"]
            client.headers["MCP-Protocol-Version"] = "2025-11-25"
            assert (
                await client.post(
                    "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
                )
            ).status_code == 202
            for number, name, args in [
                (2, "list_photos", {}),
                (3, "view_photo", {"photo_id": "p1"}),
            ]:
                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": number,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": args},
                    },
                )
                result = rpc_response(response)["result"]
                assert not result.get("isError", False)
            assert any(c["type"] == "image" for c in result["content"])
            assert (
                await client.post("/mcp", json={}, headers={"Origin": "https://evil.example"})
            ).status_code == 403
            await client.delete("/mcp")


@pytest.mark.parametrize("token", ["", "short", "a" * 32 + "\n", "é" * 32])
def test_http_refuses_missing_or_invalid_secret(token):
    with pytest.raises(SpypointError, match="HTTP requires"):
        http_app(create_server(), token)


async def test_doctor_covers_all_cameras_without_leaking_credentials(
    fake, tmp_path, monkeypatch, capsys
):
    settings = Settings(username="doctor", password="SECRET", download_dir=tmp_path)
    monkeypatch.setattr(
        cli, "SpypointClient", lambda _: SpypointClient(settings, httpx.MockTransport(fake))
    )
    fake.owned += [{"id": "empty"}]
    assert await cli.doctor(settings, True) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["camera_count"] == 2
    assert [x["status"] for x in report["image_checks"]] == ["ok", "no_photos"]
    assert all(word not in output for word in ("SECRET", "private-token", "signature="))
    fake.overrides["/bucket/image.png"] = lambda r: httpx.Response(403)
    assert await cli.doctor(settings, True) == 1
    assert json.loads(capsys.readouterr().out)["image_checks"][0]["status"] == "error"


def test_client_config_contains_paths_not_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPYPOINT_PASSWORD", "SECRET")
    monkeypatch.setattr(
        sys, "argv", ["spypoint-mcp", "client-config", "--env-file", str(tmp_path / ".env")]
    )
    cli.main()
    output = capsys.readouterr().out
    entry = json.loads(output)["mcpServers"]["spypoint"]
    assert entry["command"] == sys.executable and str(tmp_path / ".env") in entry["args"]
    assert "SECRET" not in output


def test_cli_missing_file_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["spypoint-mcp", "doctor", "--env-file", str(tmp_path / "missing")]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert not captured.out and "configure" in captured.err


def test_cli_sync_resume_and_catalog(fake, tmp_path, monkeypatch, capsys):
    settings = Settings(username="cli", password="secret", download_dir=tmp_path)
    monkeypatch.setattr(cli.Settings, "load", lambda _: settings)
    monkeypatch.setattr(
        cli, "SpypointClient", lambda _: SpypointClient(settings, httpx.MockTransport(fake))
    )
    fake.owned += [{"id": "empty"}]
    monkeypatch.setattr(sys, "argv", ["spypoint-mcp", "sync", "--download"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "pending"
    monkeypatch.setattr(sys, "argv", ["spypoint-mcp", "sync", "--job-id", first["id"]])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "finished"
    monkeypatch.setattr(sys, "argv", ["spypoint-mcp", "catalog"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["photos"] == 1


def test_cli_http_does_not_start_without_token(monkeypatch, capsys):
    monkeypatch.setattr(cli.Settings, "load", lambda _: Settings())
    monkeypatch.setattr(sys, "argv", ["spypoint-mcp", "serve", "--transport", "streamable-http"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "HTTP requires" in capsys.readouterr().err
