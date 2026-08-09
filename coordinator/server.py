"""Compute Swarm controller launcher.

Import order matters: app owns the API/state, experiments attaches parameter-sweep
routes, and web attaches the dashboard. Run this module with uvicorn.
"""
from app import app
import experiments as _experiments  # noqa: F401,E402
import web as _web  # noqa: F401,E402

__all__ = ["app"]
