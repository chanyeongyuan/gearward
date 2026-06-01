"""
modules.content — Content Engine module (blueprint §4.2).

Two flows, one engine:
  Flow 1 — POC as sales motion: prospect → generate POC → deliver as pitch
  Flow 2 — Recycle lost pitches: de-identify → deploy to owned channels → flywheel

Graph: research → product_intelligence → generate → critic (loop) → novelty_check → publish
"""
from .graph import build_content_graph, ContentState

__all__ = ["build_content_graph", "ContentState"]
