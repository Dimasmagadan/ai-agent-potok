# v1.1.0 - Potok Recruiting Agent MCP Bundle

## Install in Claude Desktop

1. Download `potok-recruiting-agent.mcpb` from this GitHub Release.
2. Open it, or select `Settings -> Extensions -> Advanced settings -> Install Extension`.
3. Keep Demo mode enabled and select `Install`.

The default demo is self-contained: it uses synthetic fixtures and needs no
Potok token, repository clone, or separately running mock API. It is read-only
and does not contain real company data.

To connect a real tenant, disable Demo mode in the extension settings and enter
the Potok v3 URL and API token. The v2, Career API URL, and constructor ID are
optional settings for the corresponding tools.

## Requirements

- macOS
- Python 3.9 or later available to Claude Desktop as `python3`

This unsigned MVP bundle should be installed through Claude Desktop's extension
settings if the application requests confirmation.
