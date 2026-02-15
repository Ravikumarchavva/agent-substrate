# Spotify Playback Options - Full Analysis

## Current Issue

**Problem**: "No tracks have preview URLs available"
**Root Cause**: Spotify doesn't provide 30-second preview URLs for all tracks. This is a **Spotify API limitation**, not a bug in our code.

## Understanding Spotify's Preview URL Availability

According to [Spotify Web API Track Object](https://developer.spotify.com/documentation/web-api/reference/get-track):

> `preview_url`: A link to a 30 second preview (MP3 format) of the track. **Can be null**

### Why Some Tracks Don't Have Previews:

1. **Regional restrictions** - Track not available in market
2. **Licensing agreements** - Label hasn't provided preview
3. **New releases** - Too new, preview not generated yet
4. **Older catalog** - Legacy tracks without previews
5. **Explicit content filtering** - Based on user settings

**Reality Check**: Only ~40-60% of tracks have preview URLs available.

---

## Solution #1: Accept Preview Limitations (Current Approach)

**What we have now:**
- ✅ Client Credentials OAuth (no user login needed)
- ✅ Search any track
- ✅ Play 30-second previews when available
- ✅ No Spotify Premium required
- ✅ "Open in Spotify" fallback for tracks without previews

**Pros:**
- Simple, works out of the box
- No user authentication required
- Free tier friendly

**Cons:**
- Only plays tracks that have preview_url
- 30 seconds max
- Cannot control user's Spotify player

**Best for:** Quick music discovery, casual listening

---

## Solution #2: Upgrade to Authorization Code Flow + Web Playback SDK

**Full playback control** with user authentication.

### Requirements:
- ✅ User must log in with Spotify account
- ✅ User must have **Spotify Premium** subscription
- ✅ Browser must support Web Playback SDK

### What You Get:
- ✅ Play **full tracks** (not just 30s)
- ✅ Control user's active playback device
- ✅ Skip, pause, seek, volume control
- ✅ Access user's playlists and library
- ✅ Queue management

### Architecture Changes Needed:

```
Current (Client Credentials):
┌─────────┐          ┌──────────┐
│   App   │ ────────▶│ Spotify  │
│         │          │   API    │
└─────────┘          └──────────┘
   Search only, preview URLs

Upgraded (Authorization Code + SDK):
┌─────────┐          ┌──────────┐          ┌─────────┐
│  User   │ ◀──────▶ │   App    │ ◀──────▶ │ Spotify │
│ Browser │          │ Backend  │          │   API   │
└─────────┘          └──────────┘          └─────────┘
     │                                           │
     └─────── Web Playback SDK ─────────────────┘
       (plays full tracks in browser)
```

### Implementation Steps:

#### 1. Update Backend OAuth Flow

**File**: `src/agent_framework/services/spotify_auth.py` (NEW)

```python
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
import httpx
import secrets

class SpotifyAuthService:
    """OAuth Authorization Code flow with PKCE for user authentication."""
    
    SCOPES = [
        "streaming",              # Play tracks in Web Playback SDK
        "user-read-email",        # Read user email
        "user-read-private",      # Read subscription status
        "user-modify-playback-state",  # Control playback
        "user-read-playback-state",    # Read current playback
    ]
    
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.state_store = {}  # Use Redis in production
    
    def get_authorization_url(self) -> str:
        """Generate OAuth authorization URL."""
        state = secrets.token_urlsafe(16)
        self.state_store[state] = True
        
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "scope": " ".join(self.SCOPES),
        }
        
        return f"https://accounts.spotify.com/authorize?{urlencode(params)}"
    
    async def handle_callback(self, code: str, state: str) -> dict:
        """Exchange authorization code for access + refresh tokens."""
        if state not in self.state_store:
            raise HTTPException(400, "Invalid state")
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            resp.raise_for_status()
            return resp.json()
```

**File**: `src/agent_framework/server/routes/spotify_auth.py` (NEW)

```python
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse

router = APIRouter(prefix="/auth/spotify", tags=["spotify-auth"])

@router.get("/login")
async def spotify_login(request: Request):
    """Redirect user to Spotify OAuth page."""
    auth_service = request.app.state.spotify_auth
    auth_url = auth_service.get_authorization_url()
    return RedirectResponse(auth_url)

@router.get("/callback")
async def spotify_callback(code: str, state: str, request: Request):
    """Handle OAuth callback."""
    auth_service = request.app.state.spotify_auth
    tokens = await auth_service.handle_callback(code, state)
    
    # Store tokens in user session (use Redis or JWT)
    # Return HTML that closes popup and sends tokens to parent window
    return HTMLResponse(f"""
        <script>
            window.opener.postMessage({{
                type: 'spotify_auth_success',
                tokens: {json.dumps(tokens)}
            }}, '*');
            window.close();
        </script>
    """)
```

#### 2. Add Web Playback SDK to Frontend

**File**: `src/app/page.tsx` (ADD)

```typescript
// Add Spotify Web Playback SDK
useEffect(() => {
  if (!spotifyAccessToken) return;
  
  const script = document.createElement('script');
  script.src = 'https://sdk.scdn.co/spotify-player.js';
  script.async = true;
  document.body.appendChild(script);
  
  window.onSpotifyWebPlaybackSDKReady = () => {
    const player = new window.Spotify.Player({
      name: 'Agent Framework Player',
      getOAuthToken: cb => cb(spotifyAccessToken),
      volume: 0.8,
    });
    
    player.addListener('ready', ({ device_id }) => {
      console.log('Spotify Player ready:', device_id);
      setSpotifyDeviceId(device_id);
    });
    
    player.connect();
  };
}, [spotifyAccessToken]);
```

#### 3. Update Spotify Player MCP App

**File**: `spotify_player.html` (MODIFY)

```javascript
// Add mode toggle
let playbackMode = "preview"; // "preview" or "full"

// For full playback mode
async function playFullTrack(trackUri) {
  if (!spotifyDeviceId || !spotifyAccessToken) {
    showError("Please log in to Spotify for full playback");
    return;
  }
  
  // Play on user's Spotify device via API
  const resp = await fetch('https://api.spotify.com/v1/me/player/play', {
    method: 'PUT',
    headers: {
      'Authorization': `Bearer ${spotifyAccessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      uris: [trackUri],
      device_id: spotifyDeviceId,
    }),
  });
  
  if (!resp.ok) {
    showError("Failed to play track. Check Spotify Premium status.");
  }
}
```

### Configuration Changes:

**File**: `.env`

```bash
# OLD (Client Credentials)
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret

# NEW (Add these for Authorization Code flow)
SPOTIFY_REDIRECT_URI=http://localhost:8001/auth/spotify/callback

# Frontend needs these too
NEXT_PUBLIC_SPOTIFY_CLIENT_ID=your_client_id
```

**Spotify Dashboard Settings:**
1. Go to https://developer.spotify.com/dashboard
2. Click your app → **Settings**
3. **Redirect URIs** → Add:
   - `http://localhost:8001/auth/spotify/callback`
   - `http://localhost:3000/auth/callback` (frontend)
4. **APIs Used** → Enable:
   - ✅ Web API
   - ✅ Web Playback SDK

---

## Solution #3: Hybrid Approach (RECOMMENDED)

**Best of both worlds**: Use preview URLs when available, fall back to "Open in Spotify" link.

### Changes Needed (Minimal):

**File**: `spotify_player.html` (already has this!)

```javascript
// Already implemented:
// - Shows which tracks have previews
// - "Open in Spotify" buttons for all tracks
// - Clear messaging when no previews available
```

**Improvement**: Filter search results to prefer tracks with previews.

**File**: `services/spotify.py`

```python
async def search_tracks(
    self, 
    query: str, 
    limit: int = 20, 
    prefer_previews: bool = True,
) -> List[Dict[str, Any]]:
    """Search tracks, optionally prioritizing those with preview URLs."""
    
    # Fetch more results than needed
    data = await self._get(
        "/search",
        params={"q": query, "type": "track", "limit": min(limit * 2, 50)},
    )
    
    tracks = [self._simplify_track(t) for t in data["tracks"]["items"]]
    
    if prefer_previews:
        # Sort: tracks with preview_url first
        with_preview = [t for t in tracks if t["preview_url"]]
        without_preview = [t for t in tracks if not t["preview_url"]]
        tracks = with_preview[:limit] + without_preview[:limit - len(with_preview)]
    
    return tracks[:limit]
```

---

## Comparison Table

| Feature | Current (Client Credentials) | Full Playback (Auth Code + SDK) | Hybrid (Recommended) |
|---------|------------------------------|----------------------------------|----------------------|
| **Setup Complexity** | ✅ Simple | ❌ Complex | ✅ Simple |
| **User Login** | ✅ Not required | ❌ Required | ✅ Not required |
| **Premium Required** | ✅ No | ❌ Yes | ✅ No |
| **Track Coverage** | ⚠️ 40-60% | ✅ 100% | ⚠️ 40-60% playable |
| **Playback Length** | ⚠️ 30 seconds | ✅ Full tracks | ⚠️ 30 seconds |
| **Fallback Option** | ✅ Open in Spotify | - | ✅ Open in Spotify |
| **Implementation Time** | ✅ Done | ❌ 4-6 hours | ✅ 30 minutes |

---

## Recommended Next Steps

### Option A: Keep It Simple (5 minutes)
1. Update search to prefer tracks with previews (`prefer_previews=True`)
2. Improve error messaging in HTML
3. ✅ **Done!** Users can play some tracks, open others in Spotify

### Option B: Full Implementation (6 hours)
1. Implement Authorization Code flow backend
2. Add OAuth popup flow in frontend  
3. Integrate Web Playback SDK
4. Update MCP App to use SDK
5. Test with Premium account
6. ✅ **Done!** Full track playback for Premium users

### Option C: Regional Workaround (10 minutes)
Some markets have more preview availability. Try:

```python
# In settings.py
SPOTIFY_DEFAULT_MARKET = "US"  # Try: "US", "GB", "IN", "BR"
```

---

## Testing Current Setup

Run this in your notebook to see which tracks have previews:

```python
# Test different search queries and markets
test_queries = [
    ("latest Telugu songs", "IN"),
    ("popular telugu songs 2023", "IN"),
    ("Sid Sriram telugu", "IN"),
    ("Anirudh telugu", "IN"),
]

for query, market in test_queries:
    results = await spotify.search_tracks(query=query, limit=10, market=market)
    with_preview = sum(1 for t in results if t["preview_url"])
    print(f"{query} ({market}): {with_preview}/{len(results)} have previews")
```

---

## Bottom Line

**Your current implementation is correct.** Spotify simply doesn't provide preview URLs for all tracks. 

**Choose your path:**
- ✅ **Keep it simple**: Accept 40-60% track coverage (RECOMMENDED)
- 🚀 **Go premium**: Implement full playback for Premium users (6 hours work)
- 🎯 **Hybrid**: Current approach + better filtering

Most music apps (including third-party Spotify clients) face this exact same limitation.
