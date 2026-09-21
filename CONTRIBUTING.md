# Maintaining SPYPOINT MCP

Use Python 3.11+ and `uv sync --locked`. Run Ruff checks and the complete pytest suite
before changing release status. The CI workflow is configured for Python 3.11–3.14 on Linux; local evidence is
recorded separately in VERIFICATION.md. Coverage includes branches with an 88% floor;
coverage alone does not establish correctness or live compatibility.

Modules separate configuration, upstream HTTP, catalog persistence, resumable jobs,
photo operations, derived imagery, analysis, exports, MCP contracts and CLI/transport.
`schemas.py` contains the core public output contracts. `tests/conftest.py` is synthetic:
do not commit real account responses, photos, signed links or credentials.

For API changes, inspect current primary-source contracts and update API_COMPATIBILITY.md.
Add a regression fixture for the meaningful failure boundary. Keep owned and shared
routes distinct. Never infer pagination completeness or advance past an ambiguous
boundary. Never place arbitrary upstream bodies in exceptions or logs. All account
mutations and paid requests are excluded from this release.

For persistence changes, use an explicit SQLite user_version migration, preserve prior
state, and test restart/rollback behavior. Newer unknown schemas must fail safely.
Long work must be bounded and cancellable, with checkpoint commits after completed work.
Do not let a cancelled or failed page claim successful indexing/download coverage.

Release checks:

```sh
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest --cov=spypoint_mcp --cov-branch -q
uv build
```

Bump both pyproject.toml and `src/spypoint_mcp/__init__.py`, regenerate uv.lock, inspect
wheel/sdist membership, install the built wheel into a fresh environment, and test MCP
discovery there. Update CHANGELOG.md and VERIFICATION.md with actual outcomes. Package
sources use an explicit inclusion list to exclude credentials, local catalogs, downloads,
research bundles and virtual environments. No automatic package publication is configured.

Live checks require locally configured credentials and must not mutate the account.
`doctor --check-images` is the first check; verify shared access, date/filter behavior and
real-client image rendering separately. Do not replace pending checks with synthetic results.
