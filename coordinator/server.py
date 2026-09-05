"""Compute Swarm controller launcher.

Import order matters: app owns the API/state, pairing attaches controller-approved
worker enrollment, experiments attaches parameter-sweep routes, llm_experiments
attaches the Ollama-backed experiment assistant, ollama_ui attaches local-model
chat/status UI, kaggle_cabt attaches the Pokémon TCG CABT workload UI/API, and web
attaches the main dashboard. Run this module with uvicorn.
"""
from app import app
import pairing as _pairing  # noqa: F401,E402
import experiments as _experiments  # noqa: F401,E402
import llm_experiments as _llm_experiments  # noqa: F401,E402
import ollama_ui as _ollama_ui  # noqa: F401,E402
import agent_proxy as _agent_proxy  # noqa: F401,E402
import kaggle_cabt as _kaggle_cabt  # noqa: F401,E402
import web as _web  # noqa: F401,E402

__all__ = ["app"]
