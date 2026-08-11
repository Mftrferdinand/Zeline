# Spotify Player CLI

> Terminal Spotify playback and search via `spogo` (preferred) or `spotify_player`.

Use `spogo` **(preferred)** for Spotify playback and search from the terminal. Fall back to `spotify_player` if `spogo` is unavailable.

## Requirements

- Spotify Premium account
- Either `spogo` or `spotify_player` installed

## spogo setup

- Import cookies: `spogo auth import --browser chrome`

## Common commands (spogo)

- Search: `spogo search track "query"`
- Playback: `spogo play | pause | next | prev`
- Devices: `spogo device list`, `spogo device set "<name|id>"`
- Status: `spogo status`

## spotify_player commands (fallback)

- Search: `spotify_player search "query"`
- Playback: `spotify_player playback play | pause | next | previous`
- Connect device: `spotify_player connect`
- Like track: `spotify_player like`

## Notes

- Config folder: `~/.config/spotify-player` (e.g. `app.toml`)
- For Spotify Connect integration, set a user `client_id` in config
- TUI shortcuts are available via `?` inside the app
