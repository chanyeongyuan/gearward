"""
backends — concrete implementations of the core contracts.

Import from here at startup, inject into modules. Never import backends
from inside core/ — the dependency arrow points one way: backends → core.
"""
from .memory_postgres import PostgresMemoryService
from .harness_langgraph import LangGraphHarness
from .harness_managed import ManagedAgentsHarness
from .evaluator_llm_judge import LLMJudgeEvaluator
from .routing import select_harness

__all__ = [
    "PostgresMemoryService",
    "LangGraphHarness",
    "ManagedAgentsHarness",
    "LLMJudgeEvaluator",
    "select_harness",
]
