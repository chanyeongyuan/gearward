"""
core.evaluator — the Evaluator seam (the learning layer).

Tracing shows WHAT happened; evaluation shows whether it was GOOD. The loop:
  * offline   — score against a per-client golden set before any deploy
  * online    — LLM-as-judge scorers on a 5-10% sample of live traces
  * gate      — block deploys/merges that regress
  * feedback  — production failures auto-curate back into the golden set
  * ground    — owned-channel engagement (Flow 2) is the strongest signal: it
    truth       upgrades scoring from GUESSING virality to KNOWING it per category

Modules and harnesses depend on this abstract class, not a concrete platform
(Braintrust / extended Langfuse).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .types import AgentResult, ClientId, EvalDimension, EvalResult, Module

# Default content dimensions (blueprint §4.2 / §5.3). Non-substitutability is the
# brutal one — it forces specificity and kills generic output.
CONTENT_DIMENSIONS: list[EvalDimension] = [
    EvalDimension("hook_strength", "Creates a 'wait, what?' in 2 seconds"),
    EvalDimension("humor_personality", "Matches brand tone without being try-hard"),
    EvalDimension("fifteen_second_fit", "Executable in ~15 seconds"),
    EvalDimension("brand_specificity", "Unmistakably this brand, not a generic voice"),
    EvalDimension("higgsfield_executability", "Higgsfield can render this with current models"),
    EvalDimension("non_substitutability",
                  "Works ONLY for this product; a competitor find-and-replace would fail",
                  min_score=7.0, weight=2.0),
]


@dataclass
class GoldenCase:
    client_id: ClientId
    module: Module
    input: dict
    expected: dict           # reference / rubric the judge scores against
    tags: list[str] = field(default_factory=list)


class Evaluator(ABC):
    @abstractmethod
    def score_online(self, result: AgentResult, dimensions: list[EvalDimension]) -> EvalResult:
        """LLM-as-judge score of a single live result. Cheap model; sampled traffic."""
        ...

    @abstractmethod
    def evaluate_offline(self, cases: list[GoldenCase], dimensions: list[EvalDimension]) -> list[EvalResult]:
        """Run the agent against a golden set before deploy. Used by the CI gate."""
        ...

    @abstractmethod
    def gate(self, results: list[EvalResult], *, min_average: float = 7.0) -> bool:
        """Return True if the batch passes (safe to deploy/merge), else False."""
        ...

    @abstractmethod
    def record_engagement(self, artifact_id: str, engagement: dict[str, float]) -> None:
        """
        Attach real owned-channel performance (views/watch-time/shares) to the
        artifact's eval record. This is the ground-truth flywheel — promote
        high-engagement examples into the golden set and the Product Intelligence
        few-shots; demote low performers.
        """
        ...


class LLMJudgeEvaluator(Evaluator):
    """
    Reference backend. TODO(claude-code): inject a Tier-0/1 judge model via the
    LiteLLM gateway; implement gate() with hard floors (any dimension below its
    min_score => fail) plus the weighted average threshold; wire failures back
    into the golden-set store.
    """

    def __init__(self, *, judge) -> None:
        self._judge = judge  # callable(prompt) -> structured scores

    def score_online(self, result: AgentResult, dimensions: list[EvalDimension]) -> EvalResult:
        raise NotImplementedError("judge the result on each dimension; build fix_directive on fail")

    def evaluate_offline(self, cases, dimensions):
        raise NotImplementedError("run agent on each golden case, score, aggregate")

    def gate(self, results, *, min_average: float = 7.0) -> bool:
        raise NotImplementedError("hard floors per dimension + weighted average >= min_average")

    def record_engagement(self, artifact_id: str, engagement: dict[str, float]) -> None:
        raise NotImplementedError("persist engagement; reweight golden set / few-shots")
