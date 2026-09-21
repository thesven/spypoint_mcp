# Professional release acceptance plan

Objective: a rich, professional SPYPOINT MCP with evidence-backed capabilities and
reliable account-wide photo workflows. A universal “best” ranking cannot be proven
from public search; compare identifiable alternatives and report the search scope.

## Required delivery and evidence

| Area | Required behavior | Evidence needed | State |
| --- | --- | --- | --- |
| API compatibility | Compare current public SPYPOINT web client and community adapters; record endpoint contracts and limitations. | Source links, research date, contract tests, live account check. | Source contracts tested; owned-camera live checks passed, shared/video pending |
| Camera fleet | Owned/shared discovery, useful configuration/status, name lookup, health report, explicit partial failures. | Representative fixtures, MCP behavior tests, live multi-camera check. | Implemented and fixture-tested; live comparison pending |
| Photo exploration | Accurate metadata, filters, detail lookup, multi-camera fairness, deduplication and honest pagination. | Boundary, ordering, equal-timestamp and permission tests. | Implemented and boundary-tested; live filter check pending |
| Durable workflows | Local catalog, account isolation, resumable per-camera sync, bounded work, cancellation recovery, verified downloads. | Restart, failure, duplicate, corruption, concurrent request and checkpoint tests. | Implemented and tested |
| Analysis | Timezone-aware activity summaries and comparisons; distinguish sampled coverage from population counts. | Deterministic fixtures including DST and incomplete coverage. | Implemented and tested |
| Visual workflows | Actual image content, contact sheets, controlled resizing, metadata association and byte/pixel budgets. | Image decoding/rendering checks and MCP protocol tests. | Implemented, protocol-tested and visually checked with synthetic fixtures |
| Exports | Selected/bulk downloads and portable JSON/CSV catalog exports with manifests and hashes. | Round-trip, formula-injection, path-safety and resume tests. | Implemented and integrity/round-trip tested |
| MCP quality | Precise schemas, actionable errors, annotations, resources, prompts, progress/cancellation and discovery. | Protocol tests through supported transports and a real client. | 19 tools, stdio and authenticated HTTP tested; desktop live rendering pending |
| Operations | Private configuration, redacted diagnostics, bounded retries/timeouts, installable package, CI and maintenance documentation. | Security tests, lint/type checks, coverage analysis, built-artifact installation. | Private setup, diagnostics, CI and maintenance docs implemented; clean wheel installation verified |
| Release verification | Full offline suite plus actual account and client verification; limitations clearly surfaced. | Current command output and redacted live verification report. | Owned-account live checks passed; remaining UI/shared/video scope in VERIFICATION.md |

## Current scope

Version 0.2.0 keeps account access read-only. Existing MP4 downloads are included;
settings changes, paid HD orders, automatic scheduling, HLS/live video, multi-user
hosting and animal classification are not implemented. Incremental refresh uses a
new overlapping capture-time sync interval; delayed uploads need a wider re-scan.
See VERIFICATION.md for exact executed checks and unclosed gates.

## Baseline

Version 0.1.0 has seven tools and 49 passing synthetic/transport tests. There is no
live account validation. Date-only historical pagination cannot yet guarantee a
complete archive; that limitation must not be hidden by a successful sync status.

## Research scope

2026-09-21 GitHub repository searches: `spypoint mcp` returned zero repositories;
`spypoint api` returned five; `spypoint download` returned three. These queries do
not establish that no competing MCP exists. Compare pyspypoint, spypoint-api,
spypoint-api-wrapper, spypoint-ctrl, and current download/kiosk workflows. Inspect
the public web application for fresher contracts before adding upstream features.

## Completion policy

Do not mark the goal complete based on tool counts or synthetic tests alone.
Account-dependent checks remain explicitly pending until a live check succeeds.
Implement account mutations only with evidence for their contracts and explicit
local opt-in; do not invoke mutations against the account during development.
