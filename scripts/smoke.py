"""Exercise installed-wheel stdio discovery without account credentials."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import spypoint_mcp

module = Path(spypoint_mcp.__file__).resolve()
assert "site-packages" in module.parts, "Smoke test must use the installed wheel."


async def main():
    with tempfile.TemporaryDirectory() as directory:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "spypoint_mcp",
            "serve",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=directory,
            env={k: v for k, v in os.environ.items() if not k.startswith("SPYPOINT_")},
        )
        try:
            messages = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "wheel-smoke", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ]
            for message in messages:
                process.stdin.write((json.dumps(message) + "\n").encode())
                await process.stdin.drain()
                if "id" in message:
                    response = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
                    assert response["id"] == message["id"] and "error" not in response
            assert len(response["result"]["tools"]) == 28
            print(
                f"Installed wheel {spypoint_mcp.__version__}: stdio initialize and 28 tools passed"
            )
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()


asyncio.run(main())
