# Spotify Player Issue - Complete Diagnosis & Resolution

## 🎯 Root Cause Identified

**Your error banner is correct**: The tracks returned by Spotify truly don't have `preview_url` values.

### Test Results from Notebook:

```
✅ API Authentication: Working
✅ Search Functionality: Working  
✅ Track Metadata: Returned correctly
❌ Preview URLs: 0/10 tracks have preview_url (even for popular songs)
```

**Tested queries:**
- ❌ "Shape of You" (Ed Sheeran) - 0/10 have previews
- ❌ "Blinding Lights" (The Weeknd) - 0/10 have previews
- ❌ "telugu songs 2024" - 0/10 have previews

## 📚 Why Preview URLs Are Missing

According to [Spotify's API documentation](https://developer.spotify.com/documentation/web-api/reference/get-track):

> `preview_url`: **Nullable, Deprecated**  
> A link to a 30 second preview (MP3 format) of the track. **Can be null**

### Reasons for Missing Previews:

1. **Spotify is phasing out preview URLs** (marked "Deprecated")
2. **Regional licensing restrictions** - Especially common for:
   - Indian music (Telugu, Bollywood)
   - Certain markets where labels restrict previews
3. **Label agreements** - Some artists/labels don't provide previews
4. **New releases** - May not have previews generated yet

## ✅ Fixes Applied

### Fix #1: Updated `search_tracks()` Method

**File**: [src/agent_framework/services/spotify.py](src/agent_framework/services/spotify.py)

**Changes:**
```python
async def search_tracks(
    query: str,
    limit: int = 20,
    market: Optional[str] = None,  # ✅ Changed from ="US"
    prefer_previews: bool = True,   # ✅ New parameter
):
    # Fetch 2x tracks to find ones with previews
    fetch_limit = min(limit * 2, 50) if prefer_previews else min(limit, 50)
    
    # ... search logic ...
    
    # Sort: tracks WITH preview_url first
    if prefer_previews:
        with_preview = [t for t in tracks if t["preview_url"]]
        without_preview = [t for t in tracks if not t["preview_url"]]
        tracks = with_preview + without_preview
        
    return tracks[:limit]
```

**Benefits:**
- ✅ Searches more tracks (up to 50) to find playable ones
- ✅ Prioritizes tracks with `preview_url`
- ✅ Logs warning when no previews found
- ✅ No longer forces `market="US"` (was causing 400 errors)

### Fix #2: Removed Market Restriction

**Problem**: Your Spotify app returned `400 Bad Request` when specifying `market="US"`

**Solution**: Made `market` optional - Spotify now auto-detects based on your IP/location

### Fix #3: Updated `get_recommendations()` Consistently

Same market fix applied to recommendations endpoint.

## 🚦 Current Status

### What's Working ✅
- Spotify API authentication (Premium account, Extended Quota)
- Search returns track metadata (names, artists, album art)
- UI displays tracks correctly
- Error handling shows clear message
- "Open in Spotify" fallback for all tracks

### What's NOT Working ❌
- **Preview playback** - Spotify doesn't provide `preview_url` for ANY tracks in your searches
- This affects ALL queries (Telugu, English, international artists)

## 💡 Solutions Going Forward

### Option 1: Accept the Limitation (RECOMMENDED)
**Current setup is correct.** The lack of preview URLs is a Spotify limitation, not a bug.

**User experience:**
1. Agent searches for music
2. UI shows tracks with metadata
3. Error banner appears: "No tracks have preview URLs available"
4. User clicks "Open in Spotify" to play full tracks in Spotify app

**Pros:** Simple, works now, no changes needed  
**Cons:** Can't play tracks in-browser

---

### Option 2: Try Different Search Strategies

Some genres/markets MAY have better preview coverage. Test these:

```python
# In the agent chat, try:
"search for top global hits 2024"
"search for classic rock songs"  
"search for pop music from 2010s"
```

Older catalog and western pop music sometimes has better preview availability.

---

### Option 3: Upgrade to Full Playback (6+ hours of work)

Implement **Authorization Code flow + Web Playback SDK** for full track playback.

**Requirements:**
- ✅ Spotify Premium account (you have this)
- ✅ User must log in with Spotify OAuth
- ✅ 6+ hours of development work

**What you get:**
- ✅ Play **full tracks** (not just 30s previews)
- ✅ Control playback (play/pause/skip/volume)
- ✅ Access to user's playlists

**See**: [SPOTIFY_PLAYBACK_OPTIONS.md](SPOTIFY_PLAYBACK_OPTIONS.md) for full implementation guide

---

### Option 4: Regional Test (5 minutes)

Test if preview availability varies by your IP location:

1. Use a VPN to connect from USA/UK
2. Restart the agent
3. Try searching: "Taylor Swift", "Ed Sheeran"
4. Check if more tracks have `preview_url`

---

## 🔍 How to Debug Further

### Check Your Spotify App Settings:

1. Go to: https://developer.spotify.com/dashboard
2. Click your app → **Settings**
3. Verify:
   - ✅ App Status: "In extended quota mode"
   - ✅ APIs Used: Web API (enabled)
   - ✅ Rate limits: Check if you hit quota (unlikely)

### Run the Diagnostic Notebook:

```bash
# Open the notebook
code notebooks/spotify_api_test.ipynb

# Run all cells to test:
# - Authentication
# - Search results
# - Preview URL availability
```

### Check Agent Logs:

When you search for music, check the backend logs for:
```
⚠️ No preview URLs available for query: <your search> (market: None)
```

This confirms the SpotifyService is working correctly.

---

## 📝 Technical Details

### Preview URL Structure (when available):
```
https://p.scdn.co/mp3-preview/...
```

### API Response Example:
```json
{
  "id": "3n3Ppam7vgaVa1iaRUc9Lp",
  "name": "Mr. Brightside",
  "preview_url": "https://p.scdn.co/mp3-preview/...",  // ← Can be null
  "external_urls": {
    "spotify": "https://open.spotify.com/track/..."  // ← Always present
  }
}
```

### Error You Saw:
The red banner "No tracks have preview URLs available" is **thrown by your HTML player** when:
```javascript
const playableCount = tracks.filter(t => t.preview_url).length;
if (playableCount === 0) {
    showError("No tracks have preview URLs available. Try a different search.");
}
```

This is working as designed.

---

## 🎓 Key Learnings

1. **Preview URLs are deprecated** - Spotify is phasing them out
2. **Not all tracks have previews** - Only ~30-50% globally
3. **Regional restrictions exist** - Indian music especially affected
4. **Client Credentials flow** is correct for search (no Premium playback API)
5. **Your implementation is correct** - The limitation is on Spotify's side

---

## ✅ Recommended Action

**Keep your current setup.** It's working correctly. The error banner accurately reflects that Spotify isn't providing preview URLs.

**For users:**
- Search for music → View results → Click "Open in Spotify" to play full tracks

**For better playback:**
- Implement Authorization Code + Web Playback SDK (see Option 3 above)
- This requires ~6 hours of development work but enables full track playback

---

## 📞 Need Help?

If you want to implement full playback (Option 3), follow the detailed guide in:
[SPOTIFY_PLAYBACK_OPTIONS.md](SPOTIFY_PLAYBACK_OPTIONS.md)

Track this issue at: https://github.com/spotify/web-api/discussions
