# API compatibility and comparison scope

Research date: **2026-09-21**. This adapter uses unofficial endpoints. Public client
source is contract evidence, not a guarantee of account access, permissions, filter
semantics, historical completeness, or future compatibility.

## Primary evidence

The [official SPYPOINT web application](https://webapp.spypoint.com/) referenced
[`index-CMUauEp6.js`](https://webapp.spypoint.com/assets/index-CMUauEp6.js) when inspected.
SHA-256 of the downloaded public bundle:
`93908233184e6fba8580e5d00b85b2de7f40c9578e79074de712abc09e9b7200`.
The vendor bundle is not redistributed in this package.

Base URL: `https://restapi.spypoint.com/api/v3`.

| Contract observed in web client | Adapter behavior / limit |
| --- | --- |
| `POST /user/login` | Username/password login; token held in process memory. No interactive MFA/CAPTCHA flow. |
| `GET /camera/all` | Owned inventory; selected configuration/status fields only. |
| `GET /shared-cameras/all`, then `GET /shared-cameras/{id}` | Shared inventory; missing/inaccessible groups remain explicit warnings. |
| `POST /photo/all` | Owned bulk metadata with selected camera IDs and bounded limit. |
| `POST /shared-cameras/photos` | Separate shared-photo route. Shared filters map to tags; no custom-tag filter. |
| `GET /photo/{cameraId}/{photoId}` | Owned detail refresh, with returned camera and photo identity checks. Shared detail lookup is not assumed. |
| `GET /photo/filters?cameraModel=…` | Species identifiers, media types and time-of-day values. |
| `dateStart`, `dateEnd`; gallery ordering uses `originDate` | Capture-time bounds and normalized dates; upload time remains separate. |
| Favorite appears as `fav` in `tag` | Favorite metadata and filter translation. |
| Media descriptors contain host/path/verb | Only existing GET media on allowed HTTPS hosts; credentials never forwarded. |
| `small`, `medium`, `large`, `hd`, `sdVideo`, `hdVideo`, `streamVideo` | Images plus existing MP4 downloads; no HLS fetch or stream orchestration. |
| HD state and video duration fields | Availability/pending/error metadata; no order/purchase action. |

Mutating settings, favorites, photo deletion and HD orders were visible in the web
client but are outside this release's scope. No such requests were executed. Shared
permissions, plan-specific features, real telemetry variants, and rate limits need
live verification. Tests assert request contracts using synthetic fixtures.

## Identifiable alternatives

GitHub repository searches on the research date returned zero matches for
`spypoint mcp`, five for `spypoint api`, and three for `spypoint download`. Search
coverage is incomplete. This does not establish that competing MCPs do not exist.
The following are reference points, not a scored product ranking or a claim that
features absent from their documentation do not exist.

| Reference | Documented/source-observed role | What this project adds in its chosen scope |
| --- | --- | --- |
| [hstern/pyspypoint](https://github.com/hstern/pyspypoint) | Python authentication, camera/photo API adapter; initial photo contract reference. | MCP image blocks, explicit tool schemas and bounded workflows. |
| [happydev-ca/spypoint-api](https://github.com/happydev-ca/spypoint-api) | Camera/account integration; shared-camera discovery reference. | Shared-photo routing and integrated fleet/image exploration. |
| [coloradude/spypoint-api-wrapper](https://github.com/coloradude/spypoint-api-wrapper) | Community API wrapper identified by search. | Identified for comparison; no exhaustive feature audit or runtime comparison performed. |
| [beardface/spypoint-ctrl](https://github.com/beardface/spypoint-ctrl) | Camera settings updates and photo retrieval. | This MCP emphasizes account reads and local archives; it does not match that project's settings controls. |
| [T3flon/spypoint-ha-kiosk](https://github.com/T3flon/spypoint-ha-kiosk) | Repeated photo downloads, Home Assistant display and notifications. | Durable queryable catalog, resumable jobs, LLM-facing analysis and contact sheets; no Home Assistant notification integration. |
| [gabgosrob/spydl](https://github.com/gabgosrob/spydl), [jkp717/SpypointImageDownloader](https://github.com/jkp717/SpypointImageDownloader) | Download-oriented projects identified in search. | Referenced as adjacent workflows; not benchmarked for reliability/performance. |

The defensible objective is a well-tested, rich MCP for fleet/photo workflows. Claims
of fastest, most reliable, or universally best would require broader competitive and
real-account evidence that is not available yet.

## Live compatibility update

The 2026-09-21 account test verified password login, complete discovery of one owned
camera, owned listings/details, actual JPEG retrieval, and bounded restart/resume. Its
CDN uses `binary/octet-stream` for JPEG bodies; that content type is supported with
mandatory signature validation. The account provided no live shared-camera case and
no result for the existing-HD-video query. See VERIFICATION.md for the exact evidence.
