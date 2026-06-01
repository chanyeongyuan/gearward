"""
Content node: Publisher — routes artifact to the correct deploy target.

Uses DeploymentRouter (core/provenance.py) to enforce the IP guardrail:
  - Prospect work that lost/went stale MUST be de-identified first (Repurpose agent)
  - Owned-channel posts without de_identified=True raise IPGuardrailError

Persists the content_artifact to Postgres for engagement tracking (flywheel).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import psycopg

from core import DeploymentRouter, IPGuardrailError, Origin, PitchOutcome

if TYPE_CHECKING:
    from modules.content.graph import ContentState

_router = DeploymentRouter()


def run_publish(state: "ContentState") -> "ContentState":
    harness = state["__harness__"]
    ctx = state["__context__"]
    draft = state.get("draft", {})
    provenance = state.get("provenance")

    if provenance is None:
        # Default: treat as client work going to client channels
        from core import ContentProvenance, DeployTarget
        provenance = ContentProvenance(
            client_id=ctx.client_id,
            origin=Origin.CLIENT,
            deploy_target=DeployTarget.CLIENT_CHANNEL,
        )

    outcome = PitchOutcome(state.get("pitch_outcome", PitchOutcome.PENDING.value))
    route = _router.route(provenance, outcome)

    # Hard stop: must de-identify before deploying prospect content to owned channels
    try:
        _router.assert_deploy_allowed(provenance)
    except IPGuardrailError as e:
        return {
            **state,
            "published_artifact": None,
            "requires_human_review": True,
            "publish_error": str(e),
        }

    artifact_id = str(uuid.uuid4())

    if route.requires_repurpose:
        # Repurpose agent must run first — surface as a deferred task
        return {
            **state,
            "published_artifact": {
                "artifact_id": artifact_id,
                "status": "pending_repurpose",
                "draft": draft,
                "provenance": _provenance_to_dict(provenance),
            },
            "requires_human_review": False,
        }

    # Persist artifact with provenance for engagement tracking
    _persist_artifact(harness._db_url, artifact_id, ctx.client_id, draft, provenance)

    published = {
        "artifact_id": artifact_id,
        "deploy_target": route.target.value,
        "status": "published" if not state.get("requires_human_review") else "flagged",
        "draft": draft,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }

    # Write published hook to memory (novelty check uses this on future runs)
    from core import MemoryRecord, Module
    harness.memory.write(MemoryRecord(
        client_id=ctx.client_id,
        module=Module.CONTENT,
        content=json.dumps({"hook": draft.get("hook", ""), "artifact_id": artifact_id}),
        metadata={"node": "publisher", "deploy_target": route.target.value},
    ))

    return {**state, "published_artifact": published}


def _persist_artifact(db_url: str, artifact_id: str, client_id: str, draft: dict, provenance) -> None:
    with psycopg.connect(db_url) as conn:
        conn.execute(
            """
            INSERT INTO content_artifacts
                (id, client_id, origin, deploy_target, pitch_id, hubspot_deal_id,
                 de_identified, content, published_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                artifact_id,
                client_id,
                provenance.origin.value,
                provenance.deploy_target.value if provenance.deploy_target else None,
                provenance.pitch_id,
                provenance.hubspot_deal_id,
                provenance.de_identified,
                json.dumps(draft),
                datetime.now(timezone.utc),
            ),
        )


def _provenance_to_dict(p) -> dict:
    return {
        "client_id": p.client_id,
        "origin": p.origin.value,
        "deploy_target": p.deploy_target.value if p.deploy_target else None,
        "pitch_id": p.pitch_id,
        "hubspot_deal_id": p.hubspot_deal_id,
        "de_identified": p.de_identified,
    }
