"""
backends.evaluator_llm_judge — LLM-as-judge Evaluator.

Cheap Tier-0/1 model scores each output on the registered dimensions.
Hard floors (any dimension below min_score => fail) plus a weighted average
threshold. Production failures auto-curate back into the golden set.
Real engagement (owned-channel views/shares) upgrades scoring from guessing
virality to knowing it per category — blueprint §5.3.
"""
from __future__ import annotations

import json
import os

import litellm

from core import (
    AgentResult,
    ClientId,
    EvalDimension,
    EvalResult,
    Evaluator,
    GoldenCase,
)

_JUDGE_PROMPT = """\
You are a strict content quality judge. Score the following output on each dimension.
Return ONLY valid JSON: {{"dimension_name": score_1_to_10, ...}}
No explanation — scores only.

Output to judge:
{output}

Dimensions to score:
{dimensions}
"""


class LLMJudgeEvaluator(Evaluator):
    """
    LiteLLM-backed evaluator. Uses Tier-0 (Gemini Flash) for online scoring
    (sampled 5–10% of traffic) and Tier-1 (Haiku) for offline golden-set runs.

    Args:
        litellm_base_url: LiteLLM gateway URL.
        litellm_api_key:  Master key for the gateway.
        db_url:           Postgres connection string for persisting results
                          and curating the golden set.
    """

    ONLINE_MODEL = "tier-0"    # cheap, sampled — Gemini Flash
    OFFLINE_MODEL = "tier-1"   # Haiku for offline golden-set scoring

    def __init__(
        self,
        *,
        litellm_base_url: str,
        litellm_api_key: str,
        db_url: str,
    ) -> None:
        self._base_url = litellm_base_url
        self._api_key = litellm_api_key
        self._db_url = db_url

    # ── Evaluator interface ───────────────────────────────────────────────────

    def score_online(
        self, result: AgentResult, dimensions: list[EvalDimension]
    ) -> EvalResult:
        return self._score(result.output, dimensions, model=self.ONLINE_MODEL)

    def evaluate_offline(
        self, cases: list[GoldenCase], dimensions: list[EvalDimension]
    ) -> list[EvalResult]:
        results = []
        for case in cases:
            eval_result = self._score(case.expected, dimensions, model=self.OFFLINE_MODEL)
            results.append(eval_result)
        return results

    def gate(self, results: list[EvalResult], *, min_average: float = 7.0) -> bool:
        """
        Pass only if ALL individual results pass their hard floors AND the
        batch average meets min_average. Any single floor violation blocks.
        """
        if not results:
            return False
        return all(r.passed for r in results) and (
            sum(r.average for r in results) / len(results) >= min_average
        )

    def record_engagement(self, artifact_id: str, engagement: dict[str, float]) -> None:
        """
        Persist real owned-channel engagement and trigger golden-set promotion
        if the artifact exceeds the virality threshold.
        """
        import psycopg
        with psycopg.connect(self._db_url) as conn:
            conn.execute(
                """
                UPDATE content_artifacts
                SET engagement = %s
                WHERE id = %s
                """,
                (json.dumps(engagement), artifact_id),
            )
            # Promote high-engagement artifacts to the golden set
            virality_score = (
                engagement.get("views", 0) * 0.4
                + engagement.get("watch_time", 0) * 0.4
                + engagement.get("shares", 0) * 0.2
            )
            if virality_score >= 1000:  # tunable threshold
                self._promote_to_golden_set(conn, artifact_id, engagement)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _score(
        self,
        output: dict,
        dimensions: list[EvalDimension],
        model: str,
    ) -> EvalResult:
        dim_descriptions = "\n".join(
            f"- {d.name} (min {d.min_score}): {d.description}" for d in dimensions
        )
        prompt = _JUDGE_PROMPT.format(
            output=json.dumps(output, indent=2),
            dimensions=dim_descriptions,
        )

        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            base_url=self._base_url,
            api_key=self._api_key,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response.choices[0].message.content)

        scores: dict[str, float] = {d.name: float(raw.get(d.name, 0)) for d in dimensions}

        # Hard floor check: any dimension below its min_score is an instant fail
        fail_dims = [d for d in dimensions if scores.get(d.name, 0) < d.min_score]

        # Weighted average
        total_weight = sum(d.weight for d in dimensions)
        weighted_sum = sum(scores.get(d.name, 0) * d.weight for d in dimensions)
        average = weighted_sum / total_weight if total_weight > 0 else 0.0

        passed = not fail_dims and average >= float(os.getenv("EVAL_MIN_AVERAGE", "7.0"))

        fix_directive = None
        if fail_dims:
            fix_directive = (
                "REGENERATE. Failed dimensions: "
                + ", ".join(
                    f"{d.name}={scores.get(d.name, 0):.1f} (floor {d.min_score})"
                    for d in fail_dims
                )
            )

        return EvalResult(
            scores=scores,
            average=average,
            passed=passed,
            fail_reason=f"Hard floor violation: {[d.name for d in fail_dims]}" if fail_dims else None,
            fix_directive=fix_directive,
        )

    def _promote_to_golden_set(
        self, conn, artifact_id: str, engagement: dict[str, float]
    ) -> None:
        row = conn.execute(
            "SELECT client_id, content FROM content_artifacts WHERE id = %s",
            (artifact_id,),
        ).fetchone()
        if row is None:
            return
        client_id, content = row
        conn.execute(
            """
            INSERT INTO eval_golden_cases
                (client_id, module, agent_name, input, expected, tags, source)
            VALUES (%s, 'content', 'content_generator', %s, %s, %s, 'auto_curated')
            ON CONFLICT DO NOTHING
            """,
            (
                client_id,
                json.dumps({"artifact_id": artifact_id}),
                json.dumps(content),
                ["high_engagement", f"virality_score_{int(sum(engagement.values()))}"],
            ),
        )
