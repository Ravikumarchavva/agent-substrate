# OAuth Setup Guide – Google & Spotify

This guide provides all callback URLs and configuration steps for setting up OAuth authentication in your Next.js app.

---

## 🌐 Server Configuration

- **Frontend (Next.js)**: `http://127.0.0.1:3001`
- **Backend (FastAPI)**: `http://127.0.0.1:8001`

**Important**: Use `127.0.0.1` instead of `localhost` to avoid cookie issues across different ports.

---

## 🔐 Google OAuth Setup

### 1. Create Google OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Navigate to **APIs & Services** → **Credentials**
4. Click **Create Credentials** → **OAuth client ID**
5. Select application type: **Web application**
6. Add the following URIs:

#### Authorized JavaScript Origins:
```
http://127.0.0.1:3001
http://localhost:3001
```

#### Authorized Redirect URIs:
```
http://127.0.0.1:3001/api/auth/google/callback
http://localhost:3001/api/auth/google/callback
```

7. Copy the **Client ID** and **Client Secret**

### 2. Update `.env.local`

Add your Google OAuth credentials to `ai-chatbot-ui/.env.local`:

```bash
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here
GOOGLE_REDIRECT_URI=http://127.0.0.1:3001/api/auth/google/callback
```

### 3. Google OAuth Endpoints

- **Login**: `http://127.0.0.1:3001/api/auth/google/login`
- **Callback**: `http://127.0.0.1:3001/api/auth/google/callback`
- **Token**: `http://127.0.0.1:3001/api/auth/google/token`
- **Logout**: `http://127.0.0.1:3001/api/auth/google/logout` (POST)

### 4. Google OAuth Scopes

The following scopes are requested:
- `openid` – OpenID Connect
- `https://www.googleapis.com/auth/userinfo.email` – Email address
- `https://www.googleapis.com/auth/userinfo.profile` – Profile info (name, picture)

### 5. Usage Example

#### Frontend Login Button:
```typescript
// Open Google OAuth popup
const loginWithGoogle = () => {
  window.open(
    "http://127.0.0.1:3001/api/auth/google/login",
    "google-auth",
    "width=500,height=700"
  );
};

// Listen for auth success
window.addEventListener("message", (event) => {
  if (event.data.type === "google_auth_success") {
    console.log("User:", event.data.user);
    // Refresh your app state
  }
});
```

#### Check Authentication Status:
```typescript
const response = await fetch("http://127.0.0.1:3001/api/auth/google/token", {
  credentials: "include"
});
const data = await response.json();
console.log("Authenticated:", data.authenticated);
```

---

## 🎵 Spotify OAuth Setup

### 1. Create Spotify App

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click **Create an app**
3. Fill in app details
4. Once created, note your **Client ID** and **Client Secret**
5. Click **Edit Settings**
6. Add the following to **Redirect URIs**:

#### Redirect URIs:
```
http://127.0.0.1:3001/api/spotify/callback
http://localhost:3001/api/spotify/callback
```

7. Save settings

### 2. Update `.env.local`

Your Spotify credentials are already configured in `ai-chatbot-ui/.env.local`:

```bash
SPOTIFY_CLIENT_ID=8d03cfbeff58439da07b331ccafd669b
SPOTIFY_CLIENT_SECRET=e4102767c619435faef0f866f3fe4795
SPOTIFY_REDIRECT_URI=http://127.0.0.1:3001/api/spotify/callback
```

**⚠️ Important**: Update the Redirect URI in your Spotify Dashboard to include `http://127.0.0.1:3001/api/spotify/callback`

### 3. Spotify OAuth Endpoints

- **Login**: `http://127.0.0.1:3001/api/spotify/login`
- **Callback**: `http://127.0.0.1:3001/api/spotify/callback`
- **Token**: `http://127.0.0.1:3001/api/spotify/token`
- **Refresh**: `http://127.0.0.1:3001/api/spotify/refresh` (POST)
- **Logout**: `http://127.0.0.1:3001/api/spotify/logout` (POST)

### 4. Spotify OAuth Scopes

The following scopes are requested:
- `streaming` – Play music through Web Playback SDK
- `user-read-email` – Access user email
- `user-read-private` – Access user profile
- `user-modify-playback-state` – Control playback
- `user-read-playback-state` – Read playback state

### 5. Usage Example

The Spotify player MCP App automatically handles authentication. Users will see a "Connect Spotify" button in the player UI when not authenticated.

#### Manual Login:
```typescript
const loginWithSpotify = () => {
  window.open(
    "http://127.0.0.1:3001/api/spotify/login",
    "spotify-auth",
    "width=500,height=700"
  );
};
```

---

## 🚀 Running the Application

### 1. Start the Backend (FastAPI)

```bash
cd agent-framework/src/agent_framework
uv run .\server\app.py
```

The backend will start on `http://127.0.0.1:8001`

### 2. Start the Frontend (Next.js)

```bash
cd ai-chatbot-ui
pnpm dev
```

The frontend will start on `http://127.0.0.1:3001` (or 3000 if available)

### 3. Verify Setup

- Frontend: `http://127.0.0.1:3001`
- Backend Health: `http://127.0.0.1:8001/health`
- Google Login: `http://127.0.0.1:3001/api/auth/google/login`
- Spotify Login: `http://127.0.0.1:3001/api/spotify/login`

---

## 🔒 Security Features

### CSRF Protection
Both OAuth flows include CSRF protection via state parameters stored in httpOnly cookies.

### Secure Cookies
- `httpOnly: true` – Prevents XSS attacks
- `sameSite: "lax"` – Prevents CSRF attacks
- `secure: true` (in production) – HTTPS only
- 30-day expiry for refresh tokens
- Auto-refresh for expired access tokens

### Token Storage
All tokens are stored in httpOnly cookies (not localStorage) for maximum security.

---

## 🐛 Troubleshooting

### "Not found" Error

If you get a 404 error on `/api/spotify/login` or `/api/auth/google/login`:
1. Ensure Next.js is running on port 3001
2. Check that API route files exist in `src/app/api/`
3. Rebuild Next.js: `pnpm run build`

### Cookie Not Being Set

1. Ensure you're using `127.0.0.1` (not `localhost`) consistently
2. Check browser console for CORS errors
3. Verify `credentials: "include"` is set on fetch requests

### OAuth Redirect Mismatch

Ensure all redirect URIs in Google Cloud Console and Spotify Dashboard match exactly:
- Must use `http://127.0.0.1:3001` (with the correct port)
- Include both `127.0.0.1` and `localhost` variants for development

---

## 📋 Complete Callback URLs Summary

### Google Cloud Console – Authorized Redirect URIs:
```
http://127.0.0.1:3001/api/auth/google/callback
http://localhost:3001/api/auth/google/callback
```

### Spotify Dashboard – Redirect URIs:
```
http://127.0.0.1:3001/api/spotify/callback
http://localhost:3001/api/spotify/callback
```

### Environment Variables (`.env.local`):
```bash
# Backend API
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001

# Google OAuth
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here
GOOGLE_REDIRECT_URI=http://127.0.0.1:3001/api/auth/google/callback

# Spotify OAuth
SPOTIFY_CLIENT_ID=8d03cfbeff58439da07b331ccafd669b
SPOTIFY_CLIENT_SECRET=e4102767c619435faef0f866f3fe4795
SPOTIFY_REDIRECT_URI=http://127.0.0.1:3001/api/spotify/callback

# Session Secret (generate a random 32+ character string)
SESSION_SECRET=your_random_secret_key_min_32_chars_required_here
```

---

## ✅ Setup Checklist

- [ ] Created Google OAuth app in Google Cloud Console
- [ ] Added redirect URIs to Google OAuth app
- [ ] Created Spotify app in Spotify Dashboard
- [ ] Added redirect URIs to Spotify app (`http://127.0.0.1:3001/api/spotify/callback`)
- [ ] Updated `.env.local` with all credentials
- [ ] Generated random SESSION_SECRET (32+ chars)
- [ ] Rebuilt Next.js app: `pnpm run build`
- [ ] Started backend: `uv run .\server\app.py`
- [ ] Started frontend: `pnpm dev`
- [ ] Verified frontend runs on port 3001
- [ ] Tested Google OAuth login flow
- [ ] Tested Spotify OAuth login flow

---

**Note**: Remember to use Spotify Premium account for full track playback. The Web Playback SDK requires Premium.
