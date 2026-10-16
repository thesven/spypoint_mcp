# SPYPOINT MCP

Connect a vision-capable LLM to your SPYPOINT camera fleet. Discover owned and shared
cameras, inspect actual images, download existing photos/videos, build resumable local
archives, compare contact sheets, and analyze indexed activity with **19 MCP tools**.

**Version 0.2.0 — local release candidate.** Automated protocol, security, persistence,
and media tests pass. Live authentication, one owned camera, actual images, exports,
and restart/resume have now been verified; shared cameras and video remain unverified live.
This is an unofficial integration based on the current public SPYPOINT web client and
community adapters. See [API compatibility](API_COMPATIBILITY.md) and
[release evidence](VERIFICATION.md) for the exact scope of testing.

The server reads your SPYPOINT account. It never changes camera settings, deletes
account photos, triggers captures, or orders paid HD media. Downloads, indexing jobs,
and exports write to your local computer. One process serves one configured account.

## Contents

- [Get started](#get-started)
- [Connect your LLM](#connect-your-llm)
- [Tools](#tools)
- [Workflow examples](#workflow-examples)
- [Archive and resume](#archive-and-resume)
- [CLI reference](#cli-reference)
- [Dates, references, and coverage](#dates-references-and-coverage)
- [Configuration and privacy](#configuration-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Development and verification](#development-and-verification)

## Get started

You need a SPYPOINT account with access to a camera, Python 3.11 or newer,
[uv](https://docs.astral.sh/uv/), and an MCP-compatible client. Visual inspection also
requires a vision-capable model. Internet access is needed for account/media requests;
searching an existing local catalog does not contact SPYPOINT.

This project is delivered as source and a local wheel; it has not been published to
PyPI. Clone the repository and run these commands from its root (or use the extracted source
directory containing `pyproject.toml` and `uv.lock`):

```sh
git clone https://github.com/thesven/spypoint_mcp.git
cd spypoint_mcp
uv sync --locked
uv run spypoint-mcp configure --env-file .env
uv run spypoint-mcp doctor --env-file .env --check-images
uv run spypoint-mcp client-config --env-file .env
```

A successful doctor report has `authenticated: true`, `camera_discovery_complete: true`,
and `status: "ok"` for each camera with an accessible photo. `no_photos` is a valid empty
camera result. Review warnings and errors before relying on fleet coverage. Once connected,
ask your client to list cameras and show the latest image from each one.

The examples use a POSIX shell (macOS/Linux). Automated local checks cover macOS ARM64
with Python 3.11, 3.12, and 3.14. Hosted Linux CI passes on Python 3.11–3.14;
Windows has not been validated. See [verification evidence](VERIFICATION.md).

`configure` asks for your username and a hidden password. It creates a plaintext `.env`
with owner-only permissions on macOS/Linux and refuses to overwrite an existing file.
Keep credentials local; never paste them into chat or MCP tool arguments.

`doctor --check-images` authenticates, discovers cameras, and fetches the newest
small image by capture time from each camera into memory. It prints counts and errors,
without tokens, passwords, signed URLs, or image bytes. It saves no photos. Empty cameras
are explicit; failed discovery/retrieval returns exit code 1. Camera IDs remain in the
report, so review it before sharing.

A preexisting `SPYPOINT_TOKEN` can replace username/password, but cannot refresh itself.
Password authentication permits one refresh after an expired-token response. Interactive
MFA, CAPTCHA, and federated login are not implemented.

## Connect your LLM

Merge the generated `spypoint` entry from `client-config` into your MCP client's existing
`mcpServers` JSON configuration. It uses absolute interpreter and credential-file paths,
and contains no secrets. Regenerate it after moving the project or rebuilding its venv.
[Example JSON](examples/mcp-config.json) shows the shape.

For Codex, after setup:

```sh
codex mcp add spypoint -- "$PWD/.venv/bin/python" -m spypoint_mcp serve --env-file "$PWD/.env"
codex mcp list
```

See [example TOML](examples/codex-config.toml) and the
[official Codex MCP documentation](https://developers.openai.com/codex/mcp).
Client configuration is not modified automatically. Stdio is the default: the client
starts and stops this process. A manually started `serve` waits silently for protocol input.
Reload or restart your MCP client after adding the entry, then check that `list_cameras`
is discoverable. Saved files are on the server computer; a client on another machine
does not automatically receive files referenced by local paths.

### Optional loopback HTTP

Use HTTP only with a client that supports a custom bearer header:

1. Set a random, independent `SPYPOINT_MCP_HTTP_TOKEN` of at least 32 non-whitespace ASCII
   characters in your private environment file. Do not reuse your SPYPOINT account token.
2. Run `uv run spypoint-mcp serve --env-file .env --transport streamable-http --port 8000`.
3. Connect to `http://127.0.0.1:8000/mcp` with `Authorization: Bearer <your-local-token>`.

HTTP binds only to loopback, requires authentication, and retains the SDK's host/origin
checks. This is custom bearer authentication, **not OAuth**. This release is not a hosted,
Internet-facing, or multi-user service. A vision-capable model and client that supports
MCP image blocks are required to inspect images. The server does not classify animals.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_cameras` | Owned/shared inventory, optional name/ID substring, model and selected status/configuration, explicit discovery warnings. |
| `get_camera` | Status and configuration for one accessible camera. |
| `camera_health` | Stale reported status, signal and memory thresholds; unknown measurements remain unknown. |
| `list_photos` | Live metadata across selected cameras; capture-time, species, tag, favorite, media and time-of-day filters. |
| `list_photo_tags` | Available filter identifiers, optionally for a camera model. |
| `get_photo` | Refresh an owned photo's metadata and media references by camera/photo ID. |
| `latest_photos` | Actual newest image by capture time for each camera; follow `next_offset` to cover the fleet. |
| `view_photo` | Inline image evidence, optional bounded resizing. |
| `create_contact_sheet` | Labeled comparison of 1–12 photos, selectable rendition (large by default), explicit failed tiles and ID associations. |
| `download_photo` | Save an existing image with SHA-256; never overwrite another file. |
| `download_video` | Save an existing MP4 rendition, subject to size limits; no HLS/live streaming or paid requests. |
| `search_catalog` | Offline, filtered, cursor-paginated metadata search. |
| `catalog_status` | Indexed counts and capture-time coverage per camera. |
| `start_photo_sync` | Create a durable indexing or image-download job over fixed cameras/date boundaries. |
| `continue_photo_sync` | Perform bounded work with per-camera checkpoints, progress and integrity-checked deduplication. |
| `get_sync_status` | Inspect job progress and errors after a restart. |
| `list_sync_jobs` | Find recent jobs to resume. |
| `summarize_activity` | Indexed photo counts by camera, date, hour, weekday and tag in an IANA timezone. |
| `export_catalog` | Bounded CSV/JSON metadata export with SHA-256 and a coverage manifest. |

Core inventory, photo, catalog, download, and sync tools publish explicit output schemas.
Discovery also provides `spypoint://guide`, `spypoint://catalog/status`, and the
`review_camera_fleet` / `archive_camera_photos` prompts. Tool annotations distinguish
local writes from read workflows. Reads may cache sanitized metadata locally.

Useful requests to your connected LLM:

- “Show the latest image from every camera, including cameras that failed or had no photos.”
- “Find this week's deer-tagged photos, then make a contact sheet of six examples.”
- “Index September's photos from all cameras; continue the sync until it finishes or needs attention.”
- “Compare indexed activity by camera and hour in America/Toronto. Report the coverage limits.”
- “Download this available video, and export the selected metadata to CSV.”

## Workflow examples

The following JSON shows tool names and their arguments, not raw JSON-RPC messages.
Replace placeholder IDs with values returned by tools. Never supply credentials as
arguments. Your MCP client handles the protocol envelope.

Inspect every camera in pages, following the returned `next_offset` until it is null:

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

Find photos within a capture-time interval. Use `list_photo_tags` to discover supported
species/filter identifiers; the example dates and camera ID are placeholders:

```json
{
  "tool": "list_photos",
  "arguments": {
    "camera_ids": [
      "CAMERA_ID"
    ],
    "after": "2026-09-01T00:00:00-04:00",
    "before": "2026-09-02T00:00:00-04:00",
    "species": [
      "deer"
    ],
    "limit": 20
  }
}
```

Compare selected photos using actual image content. Large images make the default
contact sheet more useful than small thumbnails; only available renditions can be used:

```json
{
  "tool": "create_contact_sheet",
  "arguments": {
    "photo_ids": [
      "PHOTO_ID_1",
      "PHOTO_ID_2"
    ],
    "size": "large",
    "columns": 2,
    "tile_width": 480
  }
}
```

Search indexed metadata offline. Follow `next_cursor` with the same filters until null.
An empty result says nothing about photos that have not been indexed:

```json
{
  "tool": "search_catalog",
  "arguments": {
    "camera_ids": [
      "CAMERA_ID"
    ],
    "tags": [
      "deer"
    ],
    "limit": 50
  }
}
```

`search_catalog` requires every supplied tag to match. Live SPYPOINT filtering is
vendor-controlled and may differ from local search semantics. The catalog is populated
by photo listings, latest-photo retrieval, detail refreshes, and sync jobs.

## Archive and resume

A sync captures the camera list and upper date boundary at creation. `start_photo_sync`
only creates a job; call `continue_photo_sync` repeatedly to do work, or use the CLI:

```sh
uv run spypoint-mcp sync --env-file .env --after 2026-09-01T00:00:00-04:00 --download --page-budget 5
uv run spypoint-mcp sync --env-file .env --job-id JOB_ID --page-budget 5
uv run spypoint-mcp catalog --env-file .env
```

Replace `JOB_ID` with the `id` printed by the first command. Add repeated
`--camera-id CAMERA_ID` options to select cameras when creating a job. Omit `--download`
for metadata-only indexing; bulk sync saves images, not videos. Use `download_video`
separately for an available MP4. MCP sync creation additionally accepts image `size`
and `page_size`; the CLI uses their defaults (`large` and 50).

Each call processes at most 20 upstream pages (default one), fairly across cameras.
Checkpoints commit after complete pages. Interrupted pages can be retried; previously
saved files are reused only after path, byte-count, and SHA-256 checks. Conflicting
concurrent checkpoints fail explicitly. There is no background scheduler.

`pending` means more calls are needed; `finished` means the observed pages were exhausted;
`partial` records incomplete initial camera discovery; `needs_attention` indicates errors
or stalled pagination. CLI returns 2 for partial/needs-attention and 0 for pending/finished;
a zero exit code alone does not prove completion. Resumed jobs retain their filters.
To refresh recent arrivals, start another job with an overlapping date range. Deduplication
avoids duplicate files, but a capture-time window can miss delayed uploads with older
capture dates. Re-scan a suitably wider range when this matters.

## CLI reference

Run `uv run spypoint-mcp --help` for the full option list and
`uv run spypoint-mcp --version` for the installed version. Use `--env-file .env` with
account/catalog commands unless supplying configuration through the process environment.

| Command | Behavior / relevant options |
| --- | --- |
| `serve` (default) | Starts stdio; `--transport streamable-http --port 8000` enables authenticated loopback HTTP. |
| `configure` | Prompts locally and creates `--env-file`; defaults to `.env` in the working directory. |
| `doctor` | Checks authentication/discovery; `--check-images` also fetches one small image per camera. |
| `client-config` | Prints JSON configuration with absolute paths; does not register or launch a client. |
| `sync` | Creates and advances a job; accepts `--camera-id`, `--after`, `--before`, `--download`, `--page-budget`. Resume with `--job-id` and `--page-budget`. |
| `catalog` | Prints local indexed photo/download/job counts without network requests. |

Exit codes: `0` means command success (including a sync still `pending`); `1` indicates
an operational/configuration error or failed doctor check; `2` indicates CLI usage errors
or a sync requiring attention/partial coverage; `130` indicates cancellation during
interactive input or a keyboard interrupt. Inspect the JSON job state as well as the exit code.

## Dates, references, and coverage

Photo listing/catalog `date` is normalized capture time from `originDate`, falling back
to upload `date` only when absent. `captured_at` and `uploaded_at` preserve their upstream values. User date
bounds require timezone offsets. **Known limitation:** some cameras encode local wall-clock capture time with a `Z`
suffix. Automatic conversion then gives incorrect local activity hours. `summarize_activity`
flags suspected capture/upload skew and supports `reinterpret_utc_as_local=true`; you can
also set `SPYPOINT_CAMERA_TIME_MODE=wall_clock_z` with `SPYPOINT_CAMERA_TIMEZONE` to
normalize mislabeled `Z` capture times during ingestion. Compare the burned-in image time
with metadata before relying on timezone-based summaries.

Upstream timestamps without an offset require an explicit
`SPYPOINT_CAMERA_TIMEZONE`; ambiguous or nonexistent DST times are rejected.

Owned queries use `dateStart`/`dateEnd`; shared cameras use a separate endpoint. Exact
boundary inclusion is vendor-dependent: use an overlapping interval and deduplicate
when an image falls exactly on a boundary. Shared custom tags are unsupported. Live filter semantics still need account verification.
`list_photos.limit` is the combined page size (maximum 100), not a quota per camera.
Omit `camera_ids` for all discovered cameras; `[]` selects none. Use `latest_photos`
for fair fleet coverage (up to five cameras per call).

Date pagination has no verified opaque upstream cursor. Reusing `next_before` can repeat
boundary photos. Sync deduplicates IDs and expands repeated pages to 100 items; if it
still cannot advance, it reports `pagination_stalled` without skipping timestamps.
Neither a finished job nor a local catalog proves complete historical retention,
all camera captures, or absence of wildlife. Activity counts are **photos**, not animals
or visits. Analysis and export report truncation and local coverage explicitly.

Signed media references live in memory for 15 minutes, at most 2,000 photos. The local
catalog stores sanitized metadata, never signed URLs. Owned references can refresh via
`get_photo`; shared references require a fresh listing after expiry/restart. No size is
silently substituted. Image sizes are `small`, `medium`, `large`, `hd`; available video
sizes are `sdVideo`, `hdVideo`, `streamVideo`, when an existing MP4 URL is supplied.

## Configuration and privacy

See [.env.example](.env.example). Only an explicitly supplied environment file is loaded;
process environment values override it. Relative paths resolve beside that file, or
against the working directory when no file is supplied.

| Setting | Default / meaning |
| --- | --- |
| `SPYPOINT_USERNAME`, `SPYPOINT_PASSWORD` | Local password login; alternatively `SPYPOINT_TOKEN`. |
| `SPYPOINT_DOWNLOAD_DIR` | `downloads`; photos, videos and exports. |
| `SPYPOINT_STATE_DIR` | `.spypoint-state`; per-credential-namespace SQLite catalog and checkpoints. |
| `SPYPOINT_INCLUDE_SHARED` | `true`; include shared cameras. |
| `SPYPOINT_CAMERA_TIMEZONE` | Unset; supply when interpreting timezone-less or `wall_clock_z` upstream timestamps. |
| `SPYPOINT_CAMERA_TIME_MODE` | `strict`; set to `wall_clock_z` with `SPYPOINT_CAMERA_TIMEZONE` for mislabeled `Z` local times. |
| `SPYPOINT_TIMEOUT_SECONDS` | 30 seconds per request; allowed 1–120. |
| `SPYPOINT_MAX_IMAGE_BYTES` | 10,000,000; allowed 1,024–50,000,000. |
| `SPYPOINT_MAX_VIDEO_BYTES` | 100,000,000; allowed 1,024–500,000,000. |
| `SPYPOINT_IMAGE_HOSTS` | Optional exact additional trusted CDN hostnames. |
| `SPYPOINT_MCP_HTTP_TOKEN` | Independent bearer token required for optional loopback HTTP. |

The adapter makes bounded retries, hides upstream error bodies and signed URLs, isolates
API auth from the media client, rejects redirects, and checks hostnames/media signatures.
Media sizes are bounded; derived images also have a decoded pixel budget. Generated
filenames and atomic writes prevent model-selected paths and accidental overwrites.
CSV cells are escaped against spreadsheet formula execution. HTTP proxy environment
variables are ignored. See [security and data handling](SECURITY.md) for threat boundaries.

Local metadata, saved images and exports remain until you remove them. Changing password
for the same username preserves the catalog namespace; replacing a token changes it.
An account namespace is not an operating-system security boundary. Stop the server before
backing up/removing its state directory. Original downloaded images can retain EXIF;
resized images and contact sheets are derivatives with metadata removed. Any images
returned through MCP are sent to your chosen client/model under that provider's policies.

## Troubleshooting

| Symptom | What to check or do |
| --- | --- |
| Credentials missing or file not found | Supply the explicit `--env-file` path. Existing `.env` files are never discovered automatically. |
| `configure` says the file exists | Edit that private file locally; the command intentionally refuses to overwrite credentials. |
| Authentication fails or a token expires | Check the account in SPYPOINT's own app. Replace a token locally or configure username/password for refresh. MFA/CAPTCHA/federated login need additional adapter support. |
| Server appears to hang when run manually | Stdio waits for MCP input. Start it through the configured MCP client, or run `doctor` for a human-readable check. |
| MCP client cannot start the server | Verify the absolute interpreter/env-file paths from `client-config`; regenerate them after moving the project and reload the client. |
| Photo reference expired or requested size unavailable | List photos again, or refresh an owned photo with `get_photo`. Select a rendition in `available_sizes` / `available_video_sizes`; HD must already exist. |
| Upstream date lacks a timezone | Set a known `SPYPOINT_CAMERA_TIMEZONE`. Do not guess the camera's timezone; ambiguous DST times are rejected. |
| Partial discovery or a per-camera failure | Inspect `warnings` and camera results. Retry after checking account/share access; successful cameras do not imply complete coverage. |
| `pagination_stalled` | Preserve the checkpoint and report the boundary. Do not subtract time to force progress: that could skip images. |
| Existing download or integrity mismatch | Standalone downloads refuse overwrite. Sync reuses only verified files; inspect or move a conflicting local file before retrying. |
| Image/response exceeds its budget | Choose a smaller rendition, fewer cameras, or `view_photo.max_edge`; use a contact sheet for comparisons. |
| Rate limit or timeout | Wait as directed, lower `page_budget`/`camera_limit`, and retry. HTTP timeout is per request, not a total job deadline. |
| HTTP returns 401 | Verify the independent local bearer token and header. The SPYPOINT account token is not the MCP HTTP token. |
| HTTP rejects a host/origin | Use the documented loopback endpoint; keep SDK host/origin protection enabled. Public hosting is outside this release. |
| Catalog looks empty after a credential change | Confirm the state path and credential namespace. Changing a bearer token selects a new namespace; it does not erase previous files. |

For a reproducible issue, record the version, command/tool, redacted error and expected
behavior. Review doctor output before sharing; it includes camera IDs. Do not include
passwords, bearer tokens, signed links or private photos. Report reproducible, non-sensitive bugs in the
[issue tracker](https://github.com/thesven/spypoint_mcp/issues). For security issues,
follow [SECURITY.md](SECURITY.md) and avoid public disclosure of secrets.

## Development and verification

```sh
uv sync --locked
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest --cov=spypoint_mcp --cov-branch -q
uv build
uv run python scripts/check_dist.py
uv run --no-project --with ./dist/spypoint_mcp-0.2.0-py3-none-any.whl python scripts/smoke.py
```

The current offline suite has **112 passing tests** on the three locally tested Python
versions, with **89.95% branch-inclusive coverage** on Python 3.12. Live tests exercised
18 tools against one owned camera; shared-camera access and MP4 downloads were not
verified live. The source archive and wheel are inspected for excluded private paths,
and a fresh wheel installation is checked over stdio. These results are bounded evidence,
not a promise of compatibility with every model, plan or client.

To upgrade this source installation, stop its MCP process, preserve the private env file,
back up state/downloads, replace the source with the intended release, run `uv sync --locked`,
and restart the client. Regenerate client configuration if installation paths changed.
See [CHANGELOG.md](CHANGELOG.md) before upgrades that alter output or storage schemas.
Removing the MCP client entry and stopping the server disconnects it; local credentials,
state and downloads remain until you remove them. SPYPOINT account data is unaffected.

See [contribution guidance](CONTRIBUTING.md), [changelog](CHANGELOG.md),
[release acceptance plan](ROADMAP.md), and [verification evidence](VERIFICATION.md).
The lockfile pins dependencies; published SDK APIs are used. SPYPOINT is a third-party
trademark, and this project is not affiliated with or endorsed by SPYPOINT.
