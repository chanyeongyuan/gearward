"""
core.types — shared vocabulary for the agency platform.

Every interface (MemoryService, Harness, Evaluator, DeploymentRouter) speaks
in these types. Keep this module dependency-free (stdlib only) so it never
creates import cycles. Concrete backends import FROM here, never the reverse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

# A tenant identifier. Every read/write/run in the platform is scoped by this.
ClientId = str


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Controlled vocabularies
# --------------------------------------------------------------------------- #
class Module(str, Enum):
    SALES_TRAINING = "sales_training"
    CONTENT = "content"
    MIGRATION = "migration"


class Tier(int, Enum):
    """Model cost tiers. Default to the lowest that clears the quality bar."""
    TIER_0 = 0  # Gemini Flash      — classification, extraction, simple Q&A
    TIER_1 = 1  # Claude Haiku      — structured tasks, record transforms
    TIER_2 = 2  # Claude Sonnet     — generation, copywriting, orchestration
    TIER_3 = 3  # Claude Opus       — hard reasoning, complex mapping (rare)


class Origin(str, Enum):
    """Where a piece of generated content came from."""
    PROSPECT = "prospect"   # POC built to win a pitch (Flow 1)
    CLIENT = "client"       # work for a signed client


class DeployTarget(str, Enum):
    """Where content is deployed."""
    PROSPECT = "prospect"             # delivered as the pitch (not live-published)
    CLIENT_CHANNEL = "client_channel" # the signed client's channels
    OWNED_CHANNEL = "owned_channel"   # the agency's own social channels (Flow 2)


class PitchOutcome(str, Enum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    STALE = "stale"   # no response after the configured window


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
@dataclass
class MemoryRecord:
    """A single retrievable memory, always scoped to a client + module."""
    client_id: ClientId
    module: Module
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    record_id: Optional[str] = None          # assigned by the store on write
    score: Optional[float] = None             # relevance score on retrieval
    created_at: datetime = field(default_factory=_now)


# --------------------------------------------------------------------------- #
# Agent execution (Harness)
# --------------------------------------------------------------------------- #
@dataclass
class RunContext:
    """Carried through every agent run. Drives tenancy, tracing, and budgets."""
    client_id: ClientId
    module: Module
    run_id: Optional[str] = None              # set by the harness
    trace_id: Optional[str] = None            # links to Langfuse
    default_tier: Tier = Tier.TIER_0          # routing starts here, escalates on fail
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRequest:
    """A unit of work handed to a harness to execute."""
    agent_name: str                           # e.g. "content_generator", "schema_mapper"
    input: dict[str, Any]
    context: RunContext


@dataclass
class AgentResult:
    output: dict[str, Any]
    run_id: str
    tier_used: Tier
    token_cost: float
    trace_id: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
@dataclass
class EvalDimension:
    name: str                                 # e.g. "non_substitutability"
    description: str
    min_score: float = 5.0                    # hard floor; below this = fail
    weight: float = 1.0


@dataclass
class EvalResult:
    scores: dict[str, float]                  # dimension name -> 1..10
    average: float
    passed: bool
    fail_reason: Optional[str] = None
    fix_directive: Optional[str] = None       # injected into a regeneration loop
    # Ground-truth signal from owned-channel deployment (Flow 2), if available.
    engagement: Optional[dict[str, float]] = None  # {views, watch_time, shares, ...}


# --------------------------------------------------------------------------- #
# Content provenance (the two-flow seam)
# --------------------------------------------------------------------------- #
@dataclass
class ContentProvenance:
    """
    Tags every content artifact so it knows where it came from and where it is
    allowed to go. This is what routes the Flow-1 -> Flow-2 branch and ties
    owned-channel engagement back to the eval loop.
    """
    client_id: ClientId                       # tenant that owns the work
    origin: Origin
    deploy_target: DeployTarget
    artifact_id: Optional[str] = None
    pitch_id: Optional[str] = None            # internal pitch id (Flow 1)
    hubspot_deal_id: Optional[str] = None     # deal in YOUR own HubSpot pipeline
    de_identified: bool = False               # True once brand specifics are stripped
    created_at: datetime = field(default_factory=_now)
