"""The HTTP edge: one app, every feature router (or the subset a process's roles select), auth and problem documents."""

from loamz.server.agents.http.app import create_app

__all__ = ["create_app"]
