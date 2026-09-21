# Security and data handling

## Boundaries

This is a single-account local process. Stdio inherits the security of its parent MCP
client. Optional HTTP listens on 127.0.0.1 and requires an independent bearer secret;
it does not implement OAuth, per-user accounts, Internet deployment or TLS termination.
Keep the SDK's host/origin protection enabled. Anyone holding the local HTTP token can
invoke the tools and access the configured account's permitted data.

Credentials are supplied through an explicit file or environment, never tool inputs.
Configuration files are plaintext with owner-only permissions when created here. Account
tokens and signed media references are process memory only. Raw upstream bodies and
HTTP debug logs are excluded from normal diagnostics. Camera IDs, metadata, local paths
and imagery remain sensitive and can appear in legitimate results.

The SQLite catalog has an account/credential namespace, sanitized metadata, private
files and transactional checkpoints. It contains no account password or signed URLs.
It is not encrypted and is not an isolation mechanism against other processes running
as the same user. A token rotation produces a new catalog namespace. Keep state and
media directories on a trusted local filesystem; do not grant untrusted users write
access to those directories or their parents.

Media downloads use a separate unauthenticated HTTP client, validated HTTPS hostnames,
no redirects, byte limits and header/signature checks. Images transformed for display
also have a decoded pixel limit. Allowed S3/CloudFront patterns are necessary for the
vendor's media descriptors; they are not proof of ownership of every object on those
services. Custom CDN hosts are an administrator trust decision. The model cannot supply
a URL, output path, account secret or arbitrary HTTP request.

Camera names, tags, returned image text and other vendor data are untrusted content,
not instructions. MCP instructions/prompts reinforce this distinction. The MCP server
cannot enforce the downstream model's behavior; configure your client appropriately.
CSV export mitigates spreadsheet formula injection. Original downloads may contain EXIF
or identifying information. Image blocks go to the connected model/provider.

## Retention and backup

There is no automatic deletion or retention job. Stop the server before copying or
removing `.spypoint-state` to preserve SQLite consistency. Back up the whole state
directory and the download directory together if you need verified reuse after restore.
Removing state discards the catalog and jobs but does not delete downloaded media or
anything in SPYPOINT. Local file permissions are verified on macOS; Windows ACLs are
not validated by this release.

## Reporting a problem

The source repository is https://github.com/thesven/spypoint_mcp. No dedicated security
mailbox is configured. Contact the maintainer through your existing private channel. Provide the
version, affected tool, redacted reproduction and expected behavior. Do not include
credentials, bearer tokens, signed links, full account responses or private photos.
No vulnerability disclosure address or response SLA is implied.
