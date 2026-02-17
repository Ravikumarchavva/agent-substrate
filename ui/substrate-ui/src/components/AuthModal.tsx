"use client";

import { useAuth } from "@/contexts/AuthContext";
import { X, Check, Music, Mail } from "lucide-react";

type Props = {
  isOpen: boolean;
  onClose: () => void;
};

export function AuthModal({ isOpen, onClose }: Props) {
  const { user, googleAuth, spotifyAuth, loginWithGoogle, loginWithSpotify, logout } = useAuth();

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl shadow-2xl w-full max-w-md mx-4">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-[var(--border)]">
          <h2 className="text-xl font-semibold">Authentication</h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-[var(--card-hover)] rounded-lg transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4">
          {user && (
            <div className="flex items-center gap-3 p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-lg">
              {user.picture ? (
                <img
                  src={user.picture}
                  alt={user.name || "User"}
                  className="w-12 h-12 rounded-full"
                />
              ) : (
                <div className="w-12 h-12 rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center text-white font-bold">
                  {user.name?.charAt(0) || "U"}
                </div>
              )}
              <div className="flex-1 min-w-0">
                <p className="font-medium truncate">{user.name || "User"}</p>
                <p className="text-sm text-zinc-400 truncate">{user.email || user.provider}</p>
              </div>
            </div>
          )}

          {/* Google OAuth */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Mail className="w-5 h-5 text-blue-500" />
                <span className="font-medium">Google</span>
              </div>
              {googleAuth ? (
                <div className="flex items-center gap-1 text-emerald-500 text-sm">
                  <Check className="w-4 h-4" />
                  Connected
                </div>
              ) : (
                <span className="text-sm text-zinc-500">Not connected</span>
              )}
            </div>
            {!googleAuth && (
              <button
                onClick={loginWithGoogle}
                className="w-full py-2.5 px-4 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors flex items-center justify-center gap-2"
              >
                <Mail className="w-4 h-4" />
                Connect Google
              </button>
            )}
          </div>

          {/* Spotify OAuth */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Music className="w-5 h-5 text-green-500" />
                <span className="font-medium">Spotify</span>
              </div>
              {spotifyAuth ? (
                <div className="flex items-center gap-1 text-emerald-500 text-sm">
                  <Check className="w-4 h-4" />
                  Connected
                </div>
              ) : (
                <span className="text-sm text-zinc-500">Not connected</span>
              )}
            </div>
            {!spotifyAuth && (
              <button
                onClick={loginWithSpotify}
                className="w-full py-2.5 px-4 bg-green-600 hover:bg-green-700 text-white rounded-lg font-medium transition-colors flex items-center justify-center gap-2"
              >
                <Music className="w-4 h-4" />
                Connect Spotify Premium
              </button>
            )}
            {!spotifyAuth && (
              <p className="text-xs text-zinc-500 text-center">
                Required for full track playback
              </p>
            )}
          </div>

          {/* Logout */}
          {(googleAuth || spotifyAuth) && (
            <>
              <div className="border-t border-[var(--border)] my-4" />
              <button
                onClick={async () => {
                  await logout();
                  onClose();
                }}
                className="w-full py-2.5 px-4 bg-red-500/10 hover:bg-red-500/20 text-red-500 rounded-lg font-medium transition-colors"
              >
                Logout from All Services
              </button>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 bg-zinc-800/50 rounded-b-xl border-t border-[var(--border)]">
          <p className="text-xs text-center text-zinc-500">
            Your credentials are securely stored in httpOnly cookies
          </p>
        </div>
      </div>
    </div>
  );
}
