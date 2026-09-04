# v1.1.1 - Bugfix release

Patch release on top of the [v1.1.0](RELEASE-NOTES-v1.1.0.md) MCP Bundle. No
install steps change.

## Fixes

- `potok_reopen`/`potok_jobs_match` input schemas now match the mock server's
  actual expectations; runtime validation in `talent_pool.py` is covered by a
  parity test against every `allOf` rule in the schema.
- Telegram bot renders near-matches with gap explanations instead of
  silently dropping them.
- The `applicant_url_template` scheme check accepts `HTTPS://` case-insensitively.
- `target_job_description`'s schema description now tells the calling model
  explicitly that the tool does not parse free text — `current_criteria`
  must be extracted and confirmed with the recruiter before calling.

## Install in Claude Desktop

Same as v1.1.0: download `potok-recruiting-agent.mcpb` from this GitHub
Release, open it or use `Settings -> Extensions -> Advanced settings ->
Install Extension`, keep Demo mode enabled, select `Install`.
