# Spotify Web Playback SDK Setup Guide

This guide shows how to set up the Spotify Web Playback SDK integration for **full track playback** (not just 30-second previews).

## ✅ Requirements

1. ✅ **Spotify Premium account** - Web Playback SDK requires Premium subscription
2. ✅ **Spotify Developer App** - Get credentials from [Spotify Dashboard](https://developer.spotify.com/dashboard)
3. ✅ **Backend server** running on `http://localhost:8001`
4. ✅ **Frontend UI** running on `http://localhost:3000`

---

## 🔧 Step 1: Configure Spotify App

### 1.1 Create/Update Spotify App

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click on your app (or create a new one)
3. Click **Settings**

### 1.2 Add Redirect URI

Under **Redirect URIs**, add:
```
http://localhost:8001/auth/spotify/callback
```

Click **Save**.

### 1.3 Enable APIs

Check that these are enabled in your app settings:
- ✅ **Web API**
- ✅ **Web Playback SDK**

---

## 🔐 Step 2: Update Environment Variables

### Backend (.env in agent-framework/)

```bash
# Spotify API Credentials
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_REDIRECT_URI=http://localhost:8001/auth/spotify/callback
```

### Frontend (.env.local in ai-chatbot-ui/)

```bash
# Spotify Client ID (for frontend OAuth flow)
NEXT_PUBLIC_SPOTIFY_CLIENT_ID=your_client_id_here
```

---

## 🚀 Step 3: Start Servers

```bash
# Terminal 1 - Backend
cd agent-framework
uv run uvicorn agent_framework.server.app:app --reload --port 8001

# Terminal 2 - Frontend  
cd ai-chatbot-ui
pnpm dev
```

---

## 🎵 Step 4: Use the Player

### In the Chat UI:

1. **Search for music:**
   ```
   "search for Taylor Swift songs"
   "find relaxing jazz music"
   "show me top Telugu hits 2024"
   ```

2. **Spotify Player will appear** with a green banner: "Log in with Spotify Premium to play full tracks"

3. **Click "Connect Spotify"** button
   - OAuth popup opens
   - Log in with your Spotify Premium account
   - Grant permissions
   - Popup closes automatically

4. **Now you can play full tracks!**
   - Click any track to play
   - Use controls: play/pause, next/previous, shuffle, repeat
   - See album art and track info
   - Volume control

---

## 🔄 Architecture Flow

### OAuth Flow (happens once):

```
User clicks "Connect Spotify"
    ↓
Frontend opens popup → http://localhost:8001/auth/spotify/login
    ↓
Backend redirects → https://accounts.spotify.com/authorize
    ↓
User grants permissions
    ↓
Spotify redirects → http://localhost:8001/auth/spotify/callback?code=...
    ↓
Backend exchanges code for access_token + refresh_token
    ↓
Backend sends tokens to popup via postMessage
    ↓
Popup closes, parent window receives tokens
    ↓
Player initializes Spotify.Player with access_token
```

### Playback Flow:

```
User clicks track in UI
    ↓
JavaScript: fetch('https://api.spotify.com/v1/me/player/play')
    with { uris: [track.uri], device_id: player_device_id }
    ↓
Spotify Web Playback SDK streams full track in browser
    ↓
Player state updates (playing, position, album art, etc.)
    ↓
UI updates with state (progress bar, now playing, etc.)
```

---

## 🐛 Troubleshooting

### "Authentication failed"
- Check that `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` are correct
- Verify redirect URI matches exactly: `http://localhost:8001/auth/spotify/callback`

### "Spotify Premium required"
- Web Playback SDK **only works with Premium accounts**
- Free accounts cannot use this feature
- Fallback: Use old preview player (spotify_player.html) for 30-second previews

### "CORS error"
- Make sure backend is running on `http://localhost:8001`
- Check CORS middleware in `app.py` allows `http://localhost:3000`

### "No tracks playing"
- Check browser console for errors
- Verify deviceId was received in 'ready' event
- Check network tab for API responses
- Try clicking play/pause again (autoplay may be blocked)

### Token expired
- Access tokens expire after 1 hour
- Backend automatically refreshes using refresh_token
- If issues persist, reconnect Spotify

---

## 📝 Technical Details

### Scopes Requested:

```python
scopes = [
    "streaming",                    # Play tracks via Web Playback SDK
    "user-read-email",              # Read user email
    "user-read-private",            # Read subscription type
    "user-modify-playback-state",   # Control playback
    "user-read-playback-state",     # Read current playback state
]
```

### Files Modified:

**Backend:**
- `src/agent_framework/services/spotify_auth.py` - OAuth service
- `src/agent_framework/server/routes/spotify_oauth.py` - OAuth endpoints
- `src/agent_framework/configs/settings.py` - Added SPOTIFY_REDIRECT_URI
- `src/agent_framework/server/app.py` - Register oauth router

**Frontend MCP App:**
- `src/agent_framework/mcp_apps/spotify_player_sdk.html` - Web Playback SDK player

**Tool:**
- `src/agent_framework/tools/mcp_app_tools.py` - SpotifyPlayerTool (uses new UI)

---

## 🔄 Switching Between Preview and Full Playback

### Use Preview Player (no login needed):
```python
# In mcp_app_tools.py SpotifyPlayerTool
_meta={
    "ui": {
        "resourceUri": "ui://spotify_player",  # 30-second previews
    }
}
```

### Use Full Playback SDK (Premium + login):
```python
# In mcp_app_tools.py SpotifyPlayerTool
_meta={
    "ui": {
        "resourceUri": "ui://spotify_player_sdk",  # Full tracks
    }
}
```

---

## 📚 Resources

- [Spotify Web Playback SDK Docs](https://developer.spotify.com/documentation/web-playback-sdk)
- [Web Playback SDK Tutorial](https://developer.spotify.com/documentation/web-playback-sdk/tutorials/getting-started)
- [Spotify Authorization Guide](https://developer.spotify.com/documentation/web-api/concepts/authorization)
- [Web API Reference](https://developer.spotify.com/documentation/web-api)

---

## ✅ Success Checklist

- [ ] Spotify app created with Client ID & Secret
- [ ] Redirect URI added to app settings
- [ ] Environment variables set in both backend and frontend
- [ ] Backend server running on port 8001
- [ ] Frontend UI running on port 3000
- [ ] Spotify Premium account ready
- [ ] OAuth popup works (no errors in console)
- [ ] Access token received successfully
- [ ] Web Playback SDK initializes (check console for "Player ready")
- [ ] Device appears in Spotify Connect menu
- [ ] Full tracks play successfully

---

🎉 **Enjoy full track playback with the Agent Framework!**
