# SPYPOINT MCP Server

[![Validate CI](https://github.com/thesven/spypoint_mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/thesven/spypoint_mcp/actions/workflows/ci.yml)
![Python 3.11–3.14](https://img.shields.io/badge/python-3.11%E2%80%933.14-blue)
![Version 0.2.0](https://img.shields.io/badge/version-0.2.0-informational)

A read-only [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server and CLI that connects vision-capable LLMs to your **SPYPOINT cellular trail camera fleet**. Discover owned and shared cameras, inspect and crop actual trail camera images inline, build labeled contact sheets and motion-burst strips, download existing photos and MP4 videos with SHA-256 verification, maintain a resumable local SQLite archive with private field notes and telemetry history, and analyze solunar, twilight, and activity patterns across **25 MCP tools**, **2 resources**, and **2 guided prompts**.

> **Read-Only & Privacy-First by Design**
> This server only reads from your SPYPOINT account. It **never** modifies camera settings, deletes cloud photos, triggers remote captures, or orders paid HD media. Account credentials and upstream signed S3/CloudFront URLs are kept strictly in process memory and are never exposed in tool arguments, tool outputs, or the local SQLite catalog.

> **Compatibility Status (`v0.2.0`)**
> This is an unofficial integration built against the public SPYPOINT web client (`restapi.spypoint.com`) and community adapter conventions. Automated protocol, security, persistence, and media checks pass across Python 3.11–3.14 (**137 tests, >91% branch-inclusive coverage**), and live account testing has verified authentication, owned camera discovery, JPEG retrieval, contact sheets, exports, and restart/resume sync. Shared cameras and live MP4 downloads remain unverified against a live account. See [API Compatibility](API_COMPATIBILITY.md) and [Verification Evidence](VERIFICATION.md) for details.

---

## Table of Contents

- [Key Capabilities](#key-capabilities)
- [Quick Start](#quick-start)
- [Connect Your MCP Client](#connect-your-mcp-client)
- [MCP Tools, Resources, and Prompts](#mcp-tools-resources-and-prompts)
- [Workflow Examples](#workflow-examples)
- [Resumable Archiving and Sync](#resumable-archiving-and-sync)
- [CLI Reference](#cli-reference)
- [Timestamps, Media References, and Coverage Semantics](#timestamps-media-references-and-coverage-semantics)
- [Configuration and Security](#configuration-and-security)
- [Troubleshooting](#troubleshooting)
- [Development and Verification](#development-and-verification)

---

## Key Capabilities

- **Fleet Discovery, Telemetry Health & Battery Trends**: Enumerate owned and shared cameras, inspect battery/signal/SD-card telemetry (`camera_health`), and persist historical telemetry snapshots (`telemetry_trends`) to compute linear battery discharge slopes (`%/day`) and estimated days until `20%` and `0%` battery.
- **Inline Visual Inspection, ROI Zoom & Motion Burst Strips**: Return actual JPEG/PNG/WebP images as MCP `ImageContent` blocks (`latest_photos`, `view_photo`), crop and upscale normalized regions of interest (`crop_photo`) to inspect antlers, ear tags, or distant targets, build 1–12 photo comparison grids (`create_contact_sheet`), or auto-cluster chronological motion bursts into horizontal delta-labeled strips (`create_burst_strip`).
- **Cold-Session Media Downloads**: Save existing `small`, `medium`, `large`, or `hd` photos (`download_photo`) and `sdVideo`, `hdVideo`, or `streamVideo` MP4 clips (`download_video`) to a dedicated local directory with atomic writes and SHA-256 integrity records.
- **Account-Scoped Local SQLite Catalog (`schema_version=3`) & Private Field Notes**: Automatically indexes sanitized photo metadata and camera names (`cameras` table) isolated per credential namespace. Attach private local field notes and normalized custom tags (`annotate_photo`) and query offline (`search_catalog`) by camera, capture-time window, upstream `tags`, private `user_tags`, favorites, and video availability (`has_video`).
- **Durable Bulk Sync & Incremental High-Water Mark Polling**: Create checkpointed indexing or image-download jobs (`start_photo_sync`, `continue_photo_sync`) that survive process restarts, or run single-call incremental polling (`check_new_photos`) that tracks per-camera high-water marks in SQLite and returns only newly arrived photos since the last check.
- **Timezone-Aware Solunar, Twilight & Activity Analytics**: Summarize photo counts by camera (`camera_name`), day, local hour, weekday, lunar phase (`by_moon_phase`), and solar twilight phase (`by_sun_phase`) in any IANA timezone (`summarize_activity`, `solunar_summary`), automatically detect mislabeled wall-clock `Z` timestamps, and export bounded CSV/JSON snapshots with formula-injection protection and SHA-256 coverage manifests (`export_catalog`).

---

## Quick Start

### Prerequisites

- A **SPYPOINT account** with at least one accessible camera
- **Python 3.11 or newer** and [**uv**](https://docs.astral.sh/uv/)
- An **MCP-compatible client** (e.g., Claude Desktop, Codex CLI, Cursor, Windsurf). Visual inspection requires a vision-capable model.

### Installation & Setup

Clone the repository and run the interactive setup from the project root:

```sh
git clone https://github.com/thesven/spypoint_mcp.git
cd spypoint_mcp
uv sync --locked

# 1. Create a local owner-only .env file (prompts for username & hidden password)
uv run spypoint-mcp configure --env-file .env

# 2. Verify authentication, camera discovery, and live image retrieval
uv run spypoint-mcp doctor --env-file .env --check-images

# 3. Print the ready-to-merge MCP client JSON configuration
uv run spypoint-mcp client-config --env-file .env
```

### What the Setup Commands Do

1. **`configure`**: Prompts for your SPYPOINT email/username and hidden password, creating `.env` with `0600` (owner-only) permissions on macOS/Linux. It refuses to overwrite an existing file. Keep credentials local—never paste them into chat or MCP tool arguments.
2. **`doctor --check-images`**: Authenticates against SPYPOINT, discovers accessible cameras, and fetches the newest small image by capture time from each camera into memory (saving no files). A healthy report prints `authenticated: true`, `camera_discovery_complete: true`, and `status: "ok"` (or `no_photos` for empty cameras). Failed discovery or image retrieval exits with code `1`. Review the output before sharing, as it includes camera IDs.
3. **`client-config`**: Outputs the `mcpServers` JSON snippet using absolute paths to your virtual environment's Python interpreter and `.env` file, without embedding any secrets.

> **Authentication Note**: Password authentication automatically performs one token refresh if a bearer token expires mid-session. You can alternatively supply a pre-existing `SPYPOINT_TOKEN` in `.env`, though static tokens cannot self-refresh. Interactive MFA, CAPTCHA, and federated OAuth logins are not supported.

---

## Connect Your MCP Client

### Standard `mcpServers` JSON (Claude Desktop, Cursor, etc.)

Merge the `spypoint` entry printed by `uv run spypoint-mcp client-config --env-file .env` into your MCP client's configuration file (see [examples/mcp-config.json](examples/mcp-config.json)):

```json
{
  "mcpServers": {
    "spypoint": {
      "command": "/absolute/path/to/spypoint_mcp/.venv/bin/python",
      "args": [
        "-m",
        "spypoint_mcp",
        "serve",
        "--env-file",
        "/absolute/path/to/spypoint_mcp/.env"
      ]
    }
  }
}
```

### OpenAI Codex CLI

Register the server directly with the Codex CLI (see [examples/codex-config.toml](examples/codex-config.toml)):

```sh
codex mcp add spypoint -- "$PWD/.venv/bin/python" -m spypoint_mcp serve --env-file "$PWD/.env"
codex mcp list
```

After updating your client configuration, restart or reload the client and verify that `list_cameras` is available. By default, `serve` uses **stdio** transport and is started/stopped automatically by the MCP client.

### Optional Loopback HTTP Transport

For local MCP clients that connect over Streamable HTTP with a custom `Authorization` header:

1. Generate an independent random secret of **at least 32 non-whitespace ASCII characters** and set `SPYPOINT_MCP_HTTP_TOKEN` in your `.env` file. Do **not** reuse your SPYPOINT account token.
2. Start the server on loopback:
   ```sh
   uv run spypoint-mcp serve --env-file .env --transport streamable-http --port 8000
   ```
3. Point your client to `http://127.0.0.1:8000/mcp` with header `Authorization: Bearer <SPYPOINT_MCP_HTTP_TOKEN>`.

> Loopback HTTP binds strictly to `127.0.0.1`, enforces constant-time bearer token verification, and retains MCP SDK host/origin validation. It is not an OAuth provider or a multi-user/Internet-facing service.

---

## MCP Tools, Resources, and Prompts

All structured tools validate their responses against strict Pydantic output schemas (`extra="forbid"`) and declare MCP `ToolAnnotations` distinguishing read-only queries from local filesystem/catalog writes.

### 1. Fleet Discovery, Telemetry Health & Trends

| Tool | Mode | Description |
| --- | --- | --- |
| `list_cameras` | Read | List owned and enabled shared cameras (`name_contains` optional filter), model, status/configuration summary, and explicit discovery warnings. Updates cached camera names and telemetry snapshots in the local catalog. |
| `get_camera` | Read | Retrieve status and configuration telemetry (battery, signal, SD memory, firmware, last update) for one `camera_id` and record a telemetry snapshot. |
| `camera_health` | Read | Evaluate reported telemetry against configurable thresholds (`stale_hours=24`, `signal_threshold=20`, `memory_threshold=90`) and record telemetry snapshots. Preserves unknown measurements honestly without live-pinging cameras. |
| `telemetry_trends` | Read | Analyze persisted camera telemetry history (`camera_ids`, `days` 1–365, `refresh_live=false`) to compute linear battery discharge slopes (`%/day`), estimated days until `20%` and `0%` battery, signal min/max/latest, and SD storage progression. |

### 2. Live Photo Discovery & Visual Inspection

| Tool | Mode | Description |
| --- | --- | --- |
| `list_photos` | Read | Fetch live photo metadata across selected cameras (`limit` 1–100, `before`, `after`, `tags`, `favorites_only`, `species`, `time_of_day`, `media_types`, `custom_tags`) and index sanitized records into the local catalog. |
| `list_photo_tags` | Read | List supported species, media-type, and time-of-day filter identifiers, optionally scoped to a `camera_model`. |
| `get_photo` | Read | Fetch an owned photo's detail metadata (`camera_id`, `photo_id`) and refresh its in-memory signed media URLs without re-listing. |
| `latest_photos` | Read | Fetch the newest photo by capture time from up to 5 cameras per call (`offset`, `camera_limit`, `include_images=true`, `size="medium"`). Follow `next_offset` until `null` for complete fleet coverage. |
| `view_photo` | Read | Return an actual image (`small`, `medium`, `large`, or `hd`) as an MCP `ImageContent` block. Supports `camera_id` fallback for cold sessions and optional `max_edge` (256–4096 px) JPEG resizing. |
| `crop_photo` | Read | Crop a normalized bounding box (`x`, `y`, `width`, `height` in `0.0`–`1.0`) from a photo (`size="hd"` by default, `camera_id` fallback, `max_edge` 256–2048 px, `upscale=true`) and return a high-clarity zoomed JPEG `ImageContent` block alongside pixel coordinates. |
| `create_contact_sheet` | Read | Concurrently fetch 1–12 distinct `photo_ids` and render a numbered comparison sheet (`columns` 1–4, `tile_width` 256–768 px, `size="large"`). Failed tiles are rendered and reported explicitly. |
| `create_burst_strip` | Read | Render a chronological horizontal motion-burst strip from explicit `photo_ids` (1–8) or by auto-clustering photos stored in the local catalog within `burst_window_seconds` (5–600s, default 60s) of `anchor_photo_id`, labeling each frame with `+Xs` capture delta. |

### 3. Local Media Downloads

| Tool | Mode | Description |
| --- | --- | --- |
| `download_photo` | Write | Save an existing image rendition (`size="large"` by default; accepts optional `camera_id` fallback after restart) to `SPYPOINT_DOWNLOAD_DIR` with SHA-256 verification. Never overwrites different existing files. |
| `download_video` | Write | Save an existing MP4 rendition (`sdVideo`, `hdVideo`, or `streamVideo`; accepts optional `camera_id` fallback) up to `SPYPOINT_MAX_VIDEO_BYTES` with a SHA-256 record. Rejects HLS playlists and never orders paid media. |

### 4. Local Catalog, Field Notes, Analytics & Exports

| Tool | Mode | Description |
| --- | --- | --- |
| `annotate_photo` | Write | Save or clear a private local field `note` (up to 2,000 chars) and/or custom `user_tags` (up to 20 tags) on an indexed `photo_id`. Never modifies upstream SPYPOINT account state. |
| `search_catalog` | Read | Offline, cursor-paginated search over locally indexed photos (`camera_ids`, `after`, `before`, `tags`, `user_tags`, `favorites_only`, `has_video`, `limit` 1–500, `cursor`). All supplied tags must match. |
| `catalog_status` | Read | Report local SQLite catalog totals (`photos`, `downloads`, `jobs`, `schema_version=3`) and per-camera coverage (`camera_id`, `camera_name`, `photos`, `oldest`, `newest`). |
| `summarize_activity` | Read | Aggregate local catalog activity by camera (`camera_name` included), date, local hour, weekday, tag, lunar phase (`by_moon_phase`), and solar phase (`by_sun_phase` when coordinates are provided or configured). Supports `user_tags`, `favorites_only`, `has_video`, and `reinterpret_utc_as_local`. |
| `solunar_summary` | Read | Compute a dedicated solunar and twilight activity breakdown (`by_moon_phase`, `by_sun_phase`, `by_camera`) over the local catalog using camera GPS coordinates (`latitude`, `longitude`, or `SPYPOINT_DEFAULT_LATITUDE`/`SPYPOINT_DEFAULT_LONGITUDE`). |
| `export_catalog` | Write | Export a bounded local catalog snapshot (`limit` up to 50,000; supports `user_tags`, `favorites_only`, and `has_video`) to UTF-8 CSV (including `camera_name`, `user_note`, and `user_tags` columns with formula-injection escaping) or JSON alongside a `.manifest.json` SHA-256 record. |

### 5. Resumable Sync Jobs & Incremental Polling

| Tool | Mode | Description |
| --- | --- | --- |
| `start_photo_sync` | Write | Create a durable local indexing or image-download job (`camera_ids`, `after`, `before`, `download=false`, `size="large"`, `page_size=50`). |
| `continue_photo_sync` | Write | Advance a sync job by `page_budget` (1–20 pages, default 1) fairly across cameras with transactional checkpoints and SHA-256 download deduplication. |
| `get_sync_status` | Read | Inspect a sync job's durable per-camera checkpoints, counts, and errors offline. |
| `list_sync_jobs` | Read | List recent sync jobs (`limit` 1–100) for the active credential namespace so interrupted archives can be resumed. |
| `check_new_photos` | Write | Poll selected cameras (`camera_ids`, `max_pages_per_camera` 1–5, `include_images=false`, `size="medium"`) for photos newer than each camera's persisted SQLite high-water mark, indexing and returning only newly arrived photos. |

### MCP Resources & Prompts

| URI / Name | Type | Purpose |
| --- | --- | --- |
| `spypoint://guide` | Resource | Workflow guidance, pagination rules, and interpretation boundaries for LLM agents. |
| `spypoint://catalog/status` | Resource | JSON snapshot of local catalog counts and per-camera date coverage. |
| `review_camera_fleet` | Prompt | Guided workflow to inspect fleet health and view the latest photo from every camera. |
| `archive_camera_photos` | Prompt | Guided workflow (`after`, `before`) to run a checkpointed local sync until completion or attention. |

---

## Workflow Examples

### Example Prompts for Your LLM

- *"Check camera health across my fleet, then show the latest photo from every camera."*
- *"Find this week's deer-tagged photos and build a 2-column contact sheet of the top 6 images."*
- *"Index all photos from September into the local catalog, continuing the sync until finished."*
- *"Summarize indexed camera activity in `America/Toronto` and warn me if any cameras appear to use local wall-clock `Z` timestamps."*
- *"Search my local catalog for favorite clips that have video available, download the `sdVideo` MP4, and export the matching metadata to CSV."*

### Tool Call Payload Examples

The JSON snippets below show MCP tool names and arguments (replace placeholder IDs with values returned by discovery tools):

**1. Inspect the latest photo across cameras (paginate using `next_offset`):**

```json
{
  "tool": "latest_photos",
  "arguments": {
    "offset": 0,
    "camera_limit": 5,
    "include_images": true,
    "size": "medium"
  }
}
```

**2. Render a high-resolution comparison contact sheet:**

```json
{
  "tool": "create_contact_sheet",
  "arguments": {
    "photo_ids": ["PHOTO_ID_1", "PHOTO_ID_2", "PHOTO_ID_3", "PHOTO_ID_4"],
    "size": "large",
    "columns": 2,
    "tile_width": 480
  }
}
```

**3. Search the local catalog offline for photos with available video:**

```json
{
  "tool": "search_catalog",
  "arguments": {
    "camera_ids": ["CAMERA_ID"],
    "tags": ["deer"],
    "favorites_only": false,
    "has_video": true,
    "limit": 50
  }
}
```

**4. Summarize activity in local time with wall-clock `Z` reinterpretation:**

```json
{
  "tool": "summarize_activity",
  "arguments": {
    "timezone_name": "America/Toronto",
    "after": "2026-09-01T00:00:00-04:00",
    "before": "2026-10-01T00:00:00-04:00",
    "reinterpret_utc_as_local": true,
    "limit": 10000
  }
}
```

---

## Resumable Archiving and Sync

Bulk archiving is split into job creation (`start_photo_sync`) and bounded step execution (`continue_photo_sync`) so long transfers never time out an MCP request. You can also drive sync jobs directly from the CLI:

```sh
# Create a sync job and process the first 5 upstream pages
uv run spypoint-mcp sync --env-file .env --after 2026-09-01T00:00:00-04:00 --download --page-budget 5

# Resume an existing job by ID
uv run spypoint-mcp sync --env-file .env --job-id JOB_ID --page-budget 5

# Inspect local catalog coverage offline
uv run spypoint-mcp catalog --env-file .env
```

- **Fairness & Checkpoints**: Each step processes between 1 and 20 upstream pages round-robin across selected cameras and commits to SQLite after every page.
- **Integrity-Verified Deduplication**: When `download=true` (`--download`), existing files on disk are reused only if their path, byte length, and SHA-256 digest match the recorded download manifest.
- **Job Statuses**:
  - `pending`: More pages remain; call `continue_photo_sync` (or rerun `spypoint-mcp sync --job-id ...`) to continue.
  - `finished`: All observed pages within the capture-time window were exhausted.
  - `partial`: Initial camera discovery was incomplete, though accessible cameras finished.
  - `needs_attention`: One or more cameras encountered an upstream error or `pagination_stalled`.

---

## CLI Reference

Run `uv run spypoint-mcp --help` for full command usage or `uv run spypoint-mcp --version` for the package version.

| Command | Description & Key Options |
| --- | --- |
| `serve` *(default)* | Run the MCP server over `stdio` (default) or `--transport streamable-http --port 8000` (loopback `127.0.0.1` only). |
| `configure` | Prompt interactively for SPYPOINT credentials and write a new `0600` `--env-file` (default `.env`). |
| `doctor` | Validate credentials and camera discovery; add `--check-images` to verify latest-photo retrieval per camera. |
| `client-config` | Print the `mcpServers` JSON snippet with resolved interpreter and `--env-file` paths. |
| `sync` | Start (`--camera-id`, `--after`, `--before`, `--download`) or resume (`--job-id`) a local sync job with `--page-budget` (1–20). |
| `catalog` | Print local SQLite catalog counts and per-camera date coverage without making network calls. |

**Exit Codes**:
- `0`: Success (including a `sync` command whose job status is `pending` or `finished`).
- `1`: Operational, network, or configuration error, or a failed `doctor` check.
- `2`: CLI argument error, or a `sync` job ending in `partial` / `needs_attention`.
- `130`: Interrupted via `Ctrl+C` or EOF during interactive prompts.

---

## Timestamps, Media References, and Coverage Semantics

### Capture Time vs. Upload Time & Wall-Clock `Z` Skew

- Photo `date` in tool outputs and the local catalog is normalized **capture time** (`originDate`), falling back to upload time (`date`) only if `originDate` is absent. Both raw upstream values are preserved in `captured_at` and `uploaded_at`.
- User-supplied `after` and `before` filters require explicit ISO-8601 timezone offsets (e.g., `2026-09-01T00:00:00-04:00` or `Z`).
- **Naive & Wall-Clock `Z` Camera Timestamps**:
  - If a camera reports timezone-naive timestamps, set `SPYPOINT_CAMERA_TIMEZONE` to a valid IANA timezone (e.g., `America/Toronto`). Ambiguous or nonexistent DST transitions are rejected explicitly.
  - Some SPYPOINT cameras encode local wall-clock capture time with a literal `Z` suffix while `uploaded_at` is true UTC.
    - At **ingestion time**, you can set `SPYPOINT_CAMERA_TIME_MODE=wall_clock_z` alongside `SPYPOINT_CAMERA_TIMEZONE` so `Z`-suffixed capture timestamps are reinterpreted in the configured camera timezone.
    - At **analysis time**, `summarize_activity` automatically compares `captured_at` and `uploaded_at` to flag `suspected_wall_clock_z_photos` and emit `timestamp_warnings`, and supports `reinterpret_utc_as_local=true` on already-indexed catalog data.

### Signed Media Reference Lifetime & Cold Sessions

- Upstream signed image and video URLs are cached in memory for **15 minutes** (bounded to 2,000 photos) and are never written to disk.
- After a process restart or cache expiry:
  - **Owned cameras**: Pass `camera_id` to `view_photo`, `download_photo`, or `download_video` (or call `get_photo`)—or let the server resolve `camera_id` automatically from the local SQLite catalog—to refresh the photo's signed URLs on demand.
  - **Shared cameras**: SPYPOINT's web API does not expose the single-photo detail endpoint for shared cameras; re-list the shared camera via `list_photos` or `latest_photos` to refresh its signed URLs.

### Pagination & Ecological Interpretation Limits

- Date pagination relies on capture-time boundaries (`next_before`). If more than 100 photos share the exact same millisecond timestamp, the sync engine expands the page size and deduplicates IDs; if progress remains impossible, it records `pagination_stalled` rather than skipping timestamps.
- Activity counts represent **indexed photographs**, never unique individual animals, visits, or population estimates. Missing photos do not prove wildlife absence.

---

## Configuration and Security

Configuration is loaded **only** from an explicitly provided `--env-file` and/or process environment variables (which take precedence). Existing `.env` files are never auto-discovered from parent directories. See [.env.example](.env.example).

| Environment Variable | Default | Description |
| --- | --- | --- |
| `SPYPOINT_USERNAME` | *(none)* | SPYPOINT account email/username for password authentication. |
| `SPYPOINT_PASSWORD` | *(none)* | SPYPOINT account password (supports automatic 1x session refresh). |
| `SPYPOINT_TOKEN` | *(none)* | Optional pre-existing bearer token (used instead of username/password; cannot self-refresh). |
| `SPYPOINT_DOWNLOAD_DIR` | `downloads` | Directory for downloaded photos, videos, and catalog exports. |
| `SPYPOINT_STATE_DIR` | `.spypoint-state` | Directory for credential-namespaced SQLite catalogs (`catalog.sqlite3`). |
| `SPYPOINT_INCLUDE_SHARED` | `true` | Include shared cameras (`true` or `false`). |
| `SPYPOINT_CAMERA_TIMEZONE` | *(unset)* | IANA timezone (e.g., `America/Toronto`) for naive or `wall_clock_z` camera timestamps. |
| `SPYPOINT_CAMERA_TIME_MODE` | `strict` | Timestamp mode: `strict` (trust upstream offsets) or `wall_clock_z` (reinterpret `Z` as `SPYPOINT_CAMERA_TIMEZONE`). |
| `SPYPOINT_DEFAULT_LATITUDE` | *(unset)* | Optional default latitude (`-90.0` to `90.0`) for `solunar_summary` and `summarize_activity` twilight calculations. |
| `SPYPOINT_DEFAULT_LONGITUDE` | *(unset)* | Optional default longitude (`-180.0` to `180.0`) for `solunar_summary` and `summarize_activity` twilight calculations. |
| `SPYPOINT_TIMEOUT_SECONDS` | `30` | Per-request HTTP timeout in seconds (`1`–`120`, finite). |
| `SPYPOINT_MAX_IMAGE_BYTES` | `10000000` | Maximum bytes per downloaded image (`1024`–`50000000`). |
| `SPYPOINT_MAX_VIDEO_BYTES` | `100000000` | Maximum bytes per downloaded MP4 video (`1024`–`500000000`). |
| `SPYPOINT_IMAGE_HOSTS` | *(empty)* | Optional comma-separated list of additional trusted HTTPS media CDN hostnames. |
| `SPYPOINT_MCP_HTTP_TOKEN` | *(empty)* | Required bearer secret (`>= 32` ASCII chars) when using `--transport streamable-http`. |

### Security Highlights

- **Isolated Media Transport**: Media downloads use a separate unauthenticated HTTP client that rejects redirects, ignores proxy environment variables (`trust_env=False`), enforces HTTPS allowlists (S3/CloudFront + `SPYPOINT_IMAGE_HOSTS`), validates magic-byte signatures (`JPEG`, `PNG`, `WebP`, `MP4/ftyp`), and enforces decoded pixel limits (`36,000,000` px).
- **Safe Local Storage**: Output filenames are server-generated and written atomically with `0600` permissions inside `0700` directories; symlinked state/download roots are rejected. CSV exports escape cells beginning with `=`, `+`, `-`, `@`, or control characters to prevent spreadsheet formula injection.
- **Prompt-Injection Defense**: Tool instructions and prompts explicitly mark camera names, custom tags, and burned-in image text as untrusted data.

See [SECURITY.md](SECURITY.md) for full threat boundaries, retention details, and vulnerability reporting guidelines.

---

## Troubleshooting

| Symptom | Cause & Resolution |
| --- | --- |
| `Missing SPYPOINT credentials` or `.env` not found | Pass `--env-file /path/to/.env` explicitly; `.env` files are never auto-discovered. |
| `Credential file already exists` during `configure` | `configure` refuses to overwrite existing files. Edit your `.env` file directly. |
| `401` / authentication failure on SPYPOINT API | Verify credentials in the official SPYPOINT app. Replace an expired `SPYPOINT_TOKEN` or switch to `SPYPOINT_USERNAME` + `SPYPOINT_PASSWORD`. |
| `serve` appears to hang in terminal | `serve` waits silently on `stdio` for JSON-RPC input from an MCP client. Run `spypoint-mcp doctor` for interactive diagnostics. |
| `Photo reference expired or not found` | Signed media URLs expire after 15 minutes. For owned cameras, supply `camera_id` (or call `get_photo`); for shared cameras, re-run `list_photos` or `latest_photos`. |
| `Camera timestamp has no timezone` | Set `SPYPOINT_CAMERA_TIMEZONE` to your camera's IANA timezone (e.g., `America/Toronto`). |
| Activity hours look shifted by the UTC offset | Some cameras label local wall-clock time with `Z`. Pass `reinterpret_utc_as_local=true` to `summarize_activity`, or set `SPYPOINT_CAMERA_TIME_MODE=wall_clock_z` with `SPYPOINT_CAMERA_TIMEZONE`. |
| `pagination_stalled` during sync | More than 100 photos share an identical timestamp boundary. Inspect the reported timestamp; never subtract time manually to bypass the boundary, as that can silently skip photos. |
| `Combined images exceed the MCP response budget` | Reduce `camera_limit`, choose `size="small"` or `"medium"`, set `view_photo.max_edge`, or use `create_contact_sheet`. |
| Loopback HTTP returns `401` or `403` | Ensure `Authorization: Bearer <SPYPOINT_MCP_HTTP_TOKEN>` matches `.env` and that you are connecting via `http://127.0.0.1:<port>/mcp`. |
| Catalog looks empty after changing credentials | Catalogs are namespaced by `sha256(username)` or `sha256(token)`. Switching from password mode to token mode (or rotating a token) creates a fresh namespace directory under `.spypoint-state/`. |

---

## Development and Verification

Run the full lint, format, test, coverage, package-audit, and wheel smoke-test pipeline locally with `uv`:

```sh
uv sync --locked
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest -W error --cov=spypoint_mcp --cov-branch -q
uv build
uv run python scripts/check_dist.py
uv run --no-project --with ./dist/spypoint_mcp-0.2.0-py3-none-any.whl python scripts/smoke.py
```

- **Test Suite & Coverage**: **137 automated tests** pass with **91.06% branch-inclusive coverage** (enforced minimum `88%`), validated in GitHub Actions Linux CI across Python `3.11`, `3.12`, `3.13`, and `3.14`.
- **Project Documentation**:
  - [API_COMPATIBILITY.md](API_COMPATIBILITY.md) — Upstream SPYPOINT REST endpoints, payload contracts, and known limitations
  - [VERIFICATION.md](VERIFICATION.md) & [LIVE_VERIFICATION.json](LIVE_VERIFICATION.json) — Offline and live-account verification evidence
  - [SECURITY.md](SECURITY.md) — Security model, trust boundaries, and data handling
  - [CHANGELOG.md](CHANGELOG.md) — Release history and schema changes
  - [ROADMAP.md](ROADMAP.md) — Release acceptance criteria and future milestones
  - [CONTRIBUTING.md](CONTRIBUTING.md) — Contribution and verification standards

*SPYPOINT is a trademark of its respective owner. This project is an independent, unofficial integration and is not affiliated with, endorsed by, or sponsored by SPYPOINT.*
