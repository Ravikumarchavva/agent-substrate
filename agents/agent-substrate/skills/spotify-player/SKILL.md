---
name: spotify-player
description: Control Spotify playback, search for tracks, and manage queues through the agent framework's Spotify MCP integration.
version: "1.0"
license: MIT
allowed-tools: mcp_spotify_play mcp_spotify_search mcp_spotify_queue mcp_spotify_pause mcp_spotify_skip
metadata:
  author: agent-framework
  category: music
---

# Spotify Player Skill

Use this skill when the user asks to play music, control playback, search for tracks, or manage their Spotify queue.

## Prerequisites

- The Spotify MCP tool must be active and authenticated.
- The user's Spotify access token is managed by the backend credential store.

## Playback Procedures

### Play a Track or Artist

1. Call `mcp_spotify_search` with the user's query (track name, artist name, or album).
2. Present the top 3 results to the user for confirmation if ambiguous.
3. Call `mcp_spotify_play` with the selected URI.
4. Confirm to the user: "Now playing: {track} by {artist}".

### Pause / Resume

- Use `mcp_spotify_pause` to pause current playback.
- Use `mcp_spotify_play` without arguments to resume.

### Skip Track

- Call `mcp_spotify_skip` to advance to the next queued track.

### Add to Queue

1. Search for the track using `mcp_spotify_search`.
2. Call `mcp_spotify_queue` with the track URI.
3. Confirm: "Added {track} to your queue."

## Error Handling

- If the Spotify token is missing or expired, tell the user to reconnect Spotify in the settings panel.
- If no results are found, ask the user to rephrase their search.
- Do NOT retry more than 2 times on the same search query.

## Response Format

Always confirm the action taken in a friendly, concise message:
> "Playing *Bohemian Rhapsody* by Queen on Spotify 🎵"
