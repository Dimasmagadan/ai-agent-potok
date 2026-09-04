# v1.2.0 - Job name resolution for reopen

Minor release on top of the [v1.1.1](RELEASE-NOTES-v1.1.1.md) MCP Bundle. No
install steps change.

## New

- `potok_jobs_find`: resolve a job name (including archived jobs) to an ID
  before calling `potok_reopen`. Supports an optional `top` to cap the number
  of ranked matches (default 20), skips jobs missing an `id`, and returns a
  fetch error instead of a misleading empty list when the job listing could
  not be retrieved at all.

## Install in Claude Desktop

Same as v1.1.1: download `potok-recruiting-agent.mcpb` from this GitHub
Release, open it or use `Settings -> Extensions -> Advanced settings ->
Install Extension`, keep Demo mode enabled, select `Install`.
