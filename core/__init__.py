"""
core — platform contracts for the AI agency.

Build rule: service modules and agents depend on these ABSTRACT contracts, never
on concrete backends. Wire concrete implementations once at startup and inject
them. That keeps memory / harness / eval / deployment swappable.

Concrete backends live in backends/. Harness routing lives in backends/routing.py.
"""
from .types import (
    ClientId, Module, Tier, Origin, DeployTarget, PitchOutcome,
    MemoryRecord, RunContext, AgentRequest, AgentResult,
    EvalDimension, EvalResult, ContentProvenance,
)
from .memory import MemoryService
from .harness import Harness, WorkloadProfile
from .evaluator import Evaluator, GoldenCase, CONTENT_DIMENSIONS
from .provenance import DeploymentRouter, RouteDecision, RepurposeAgent, IPGuardrailError

__all__ = [
    # Types
    "ClientId", "Module", "Tier", "Origin", "DeployTarget", "PitchOutcome",
    "MemoryRecord", "RunContext", "AgentRequest", "AgentResult",
    "EvalDimension", "EvalResult", "ContentProvenance",
    # Abstract contracts
    "MemoryService",
    "Harness", "WorkloadProfile",
    "Evaluator", "GoldenCase", "CONTENT_DIMENSIONS",
    "DeploymentRouter", "RouteDecision", "RepurposeAgent", "IPGuardrailError",
]
