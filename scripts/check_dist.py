"""Verify distributable membership without loading local credential/state files."""

import tarfile
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
archives = sorted((root / "dist").glob("spypoint_mcp-0.2.0*"))
assert len(archives) == 2, "Expected a wheel and a source archive."
for path in archives:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        assert "spypoint_mcp/server.py" in names
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
        assert any(n.endswith("/README.md") for n in names)
    for name in names:
        parts = Path(name).parts
        assert not {".venv", "downloads", ".spypoint-state", ".state", "work", "__pycache__"} & set(
            parts
        )
        assert not any(p.startswith(".env") and p != ".env.example" for p in parts)
        assert not name.endswith((".sqlite", ".sqlite3", ".db", ".pyc"))
    print(f"{path.name}: {len(names)} entries, no excluded private/state paths")
