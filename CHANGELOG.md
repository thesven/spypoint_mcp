# Changelog

## 0.2.0 — 2026-09-21 (local release candidate)

- Expanded from seven to nineteen MCP tools, with core output schemas, resources, prompts
  and bounded sync progress reporting.
- Corrected capture-time ordering and split owned/shared photo endpoint routing using
  current SPYPOINT web-client evidence; added filter/detail and existing-video support.
- Added an account-scoped SQLite catalog, offline search, durable per-camera checkpoints,
  restart/cancellation recovery, optimistic concurrency checks and verified file reuse.
- Added contact sheets, bounded image resizing, timezone-aware activity summaries,
  reported fleet health, and CSV/JSON exports with integrity/coverage manifests.
- Added ROI cropping (`crop_photo`), chronological motion burst strips (`create_burst_strip`),
  solunar and twilight analytics (`solunar_summary`), telemetry health trends (`telemetry_trends`),
  private field notes and tagging (`annotate_photo`), and high-water mark polling (`check_new_photos`).
- Added environmental weather reanalysis via Open-Meteo (`get_photo_weather`, `sync_weather`, `weather_activity_summary`)
  with local SQLite caching (`schema_version=4`), 16-point wind compass directions, 3-hour barometric
  pressure trends ($\Delta P_{3h}$), and animal activity distributions across temperature bands and wind octants.
- Added authenticated loopback HTTP, sync/catalog CLI commands and version reporting.
- Added contract, persistence, security and transport tests plus package/CI guidance.
- Live account verification found and fixed generic binary JPEG content-type handling;
  contact sheets now default to large images and accept an explicit rendition.

### Upgrade from 0.1.0

Run `uv sync --locked` and restart your client. Existing credential files remain usable.
Listings now persist sanitized metadata under `.spypoint-state` unless configured otherwise.
The public `date` field now represents capture time, with `uploaded_at` exposed separately.
Outputs gain fields and schemas; clients should use discovered schemas instead of assuming
old response shapes. There is no prior persistent database to migrate from 0.1.0.
HTTP remains opt-in and requires an independent token. Previously downloaded files remain
untouched; sync can reuse an identical existing file after verifying its content.

## 0.1.0 — 2026-09-21 (initial local prototype)

Seven stdio tools for camera discovery, photo listings, inline viewing and local downloads.
49 synthetic/protocol tests. No real-account verification.
