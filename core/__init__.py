"""
core — platform contracts for the AI agency.

Build rule: service modules and agents depend on these ABSTRACT contracts, never
on concrete backends. Wire concrete implementations once, at startup, and inject
them. That is what keeps memory / harness / eval / deployment swappable.
"""
from .types import (
    ClientId, Module, Tier, Origin, DeployTarget, PitchOutcome,
    MemoryRecord, RunContext, AgentRequest, AgentResult,
    EvalDimension, EvalResult, ContentProvenance,
)
from .memory import MemoryService, PostgresMemoryService
from .harness import (
    Harness, LangGraphHarness, ManagedAgentsHarness,
    WorkloadProfile, select_harness,
)
from .evaluator import (
    Evaluator, LLMJudgeEvaluator, GoldenCase, CONTENT_DIMENSIONS,
)
from .provenance import (
    DeploymentRouter, RouteDecision, RepurposeAgent, IPGuardrailError,
)

__all__ = [
    "ClientId", "Module", "Tier", "Origin", "DeployTarget", "PitchOutcome",
    "MemoryRecord", "RunContext", "AgentRequest", "AgentResult",
    "EvalDimension", "EvalResult", "ContentProvenance",
    "MemoryService", "PostgresMemoryService",
    "Harness", "LangGraphHarness", "ManagedAgentsHarness",
    "WorkloadProfile", "select_harness",
    "Evaluator", "LLMJudgeEvaluator", "GoldenCase", "CONTENT_DIMENSIONS",
    "DeploymentRouter", "RouteDecision", "RepurposeAgent", "IPGuardrailError",
]
