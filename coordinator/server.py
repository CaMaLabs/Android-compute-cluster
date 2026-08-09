"""Compute Swarm controller launcher.

Import order matters: app owns the API/state, experiments attaches parameter-sweep
routes, llm_experiments attaches the Ollama-backed experiment assistant, and web
attaches the dashboard. Run this module with uvicorn.
"""
from app import app
import experiments as _experiments  # noqa: F401,E402
import llm_experiments as _llm_experiments  # noqa: F401,E402
import web as _web  # noqa: F401,E402

__all__ = ["app"]
