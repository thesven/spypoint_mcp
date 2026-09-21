# Release verification — 0.2.0

Evidence date: 2026-09-21. This is a local release candidate, not a claim of live
SPYPOINT compatibility, cloud deployment, or universal superiority.

## Executed checks

| Check | Result |
| --- | --- |
| macOS ARM64, Python 3.12.13: complete offline suite | 112 tests passed. |
| Python 3.12 branch-inclusive coverage | 89.95%; configured minimum 88%. Subprocess entrypoint is separately exercised but not counted as covered by the parent process. |
| macOS ARM64, isolated Python 3.11.15 | 112 tests passed. |
| macOS ARM64, isolated Python 3.14.6 | 112 tests passed. |
| Ruff lint and format | Passed for src, tests, scripts. |
| MCP protocol | Official Python SDK client, real stdio subprocess initialization/tool discovery, authenticated HTTP initialization/session/tool/image interactions passed with synthetic data. |
| Security/failure cases | Token isolation, secret redaction, media host/path/redirect/size checks, auth refresh, rate limits, malicious IDs, CSV formulas, catalog namespaces, schema failures, HTTP auth/origin checks tested. |
| Persistence | Restarts, cancellation, checkpoint conflict rollback, overlapping pages, duplicate/corrupt downloads, interrupted file-record recovery, timestamp stalls tested. |
| Visual QA | Synthetic 2×2 contact sheet rendered and visually inspected; readable numbered timestamps and a visible failed tile. EXIF orientation, decode limits and resize dimensions tested. |
| Built wheel/sdist | Built both archives; membership inspection passed. Fresh wheel installation passed stdio initialize and discovery of all 19 tools. |
| Linux CI | Passed on Python 3.11, 3.12, 3.13 and 3.14: lint, format, tests/coverage, build, package inspection and installed-wheel stdio smoke test. [Run evidence](https://github.com/thesven/spypoint_mcp/actions/runs/35619578036). |

Tests deliberately use synthetic responses and image/video fixtures. The MP4 fixture
checks transport/signature behavior, not real-world video decoding. No private photos
or vendor bundles are packaged. HTTP tests run in-process; no Internet endpoint exists.

## Executed live account checks

Using user-authorized credentials, authentication succeeded and discovery returned one
owned camera, zero shared cameras, and no discovery warnings. The doctor fetched and
validated an actual JPEG. Eighteen MCP tools were exercised successfully through the
official SDK client, including camera/status/filters, listings/detail refresh, image blocks,
contact sheets, hash-verified downloads, offline catalog/analysis, CSV/JSON exports, and
a durable job created before restarting the server and completed afterward. The bounded
sync indexed/downloaded one photo and reused its previously verified file.

Live testing exposed `binary/octet-stream` on valid JPEG downloads. The adapter now
accepts that generic binary content type while still checking media signatures; regression
tests reject non-image bodies. Medium thumbnails proved too small for useful comparison,
so contact sheets now offer an explicit size parameter and default to large. A four-photo
large contact sheet was rendered and visually inspected. No account mutations or paid
media requests were made. A favorite query and existing-HD-video query returned zero
results; that does not establish positive-match correctness or absence of all videos.

See LIVE_VERIFICATION.json for a report without account IDs, credentials, or image URLs.

## Live acceptance checklist (remaining scope explicit)

1. **Passed:** authenticated doctor and image check on the available owned camera.
2. **Pending:** independent SPYPOINT UI inventory comparison and a live shared/empty camera case.
3. **Partial:** live owned-photo retrieval and bounded capture-time sync passed; positive
   favorite/species matches, shared retrieval and independent UI timestamp comparison remain pending.
4. **Partial:** actual SDK image blocks and locally rendered contact sheets passed;
   a configured desktop MCP client session remains pending.
5. **Partial:** bounded sync restart/resume and saved-file hash verification passed;
   independent UI count comparison remains pending.
6. **Pending:** existing-HD-video query returned no results; no real MP4 download tested.

User-authorized credentials were used only for local read-only testing. No credentials
are distributed. No account mutations, paid media requests, package publication or
hosted deployment have been performed.

## Limits of this evidence

Offline tests cannot establish vendor permissions, plan-dependent behavior, throttle
policies, all camera models, retained history, Windows ACLs or performance at production
account scale. Source-contract inspection does not replace these checks. A completed
sync means observed pages were exhausted, not that every historical capture was retrieved.

## Subsequent timestamp finding

A later read-only photo analysis found that some upstream capture times carry a `Z`
suffix while matching the burned-in local camera clock. Automatic timezone conversion
can therefore shift local activity hours incorrectly. The current timezone configuration
does not override explicit offsets. This is a known unresolved compatibility limitation;
inspect image stamps before using affected time-of-day summaries for planning.

## Initial GitHub publication audit

Source was published to [thesven/spypoint_mcp](https://github.com/thesven/spypoint_mcp)
after scanning staged content and the initial commit history with Gitleaks 8.30.1
(no findings). An additional exact-value scan checked test credentials and account/photo
identifiers without printing them; none appeared in the 40 tracked project files.
Private images, SQLite catalogs, working analysis files, virtual environments, build
artifacts and credential files are outside the committed tree or ignored. The only
environment file tracked is the empty-credential `.env.example`. These checks describe
the audited publication, not a guarantee about future commits.
