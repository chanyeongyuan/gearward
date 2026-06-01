"""
core.provenance — the two-flow seam (blueprint §4.2).

Flow 1: POC content built for a prospect, delivered as the pitch.
Flow 2: lost/stale pitches are de-identified and redeployed to the agency's
        OWN social channels — turning sunk spec work into owned-audience growth
        and real engagement data for the eval loop.

The DeploymentRouter decides where a piece of content is allowed to go, and
enforces the non-negotiable IP guardrail: a prospect's branded content is NEVER
posted to owned channels until it has been de-identified.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .types import ContentProvenance, DeployTarget, Origin, PitchOutcome


class IPGuardrailError(RuntimeError):
    """Raised on any attempt to deploy branded prospect content to owned channels."""


@dataclass
class RouteDecision:
    target: DeployTarget
    requires_repurpose: bool         # True => run the Repurpose agent first
    reason: str


class DeploymentRouter:
    """
    Pure routing logic — no I/O. Given provenance + the current pitch outcome,
    decide the deploy target. Keep this deterministic and unit-tested; it is the
    legal/brand-safety chokepoint for the whole content service.
    """

    def route(self, provenance: ContentProvenance, outcome: PitchOutcome) -> RouteDecision:
        # Client work always goes to the client's channels.
        if provenance.origin is Origin.CLIENT:
            return RouteDecision(DeployTarget.CLIENT_CHANNEL, False, "client work -> client channels")

        # Prospect work (Flow 1) branches on pitch outcome.
        if outcome is PitchOutcome.PENDING:
            return RouteDecision(DeployTarget.PROSPECT, False, "pitch pending -> deliver as pitch")
        if outcome is PitchOutcome.WON:
            return RouteDecision(DeployTarget.CLIENT_CHANNEL, False, "pitch won -> now a client")
        if outcome in (PitchOutcome.LOST, PitchOutcome.STALE):
            # Flow 2 — must de-identify before it can touch owned channels.
            return RouteDecision(
                DeployTarget.OWNED_CHANNEL,
                requires_repurpose=not provenance.de_identified,
                reason="pitch lost/stale -> repurpose to owned channels",
            )
        raise ValueError(f"unhandled outcome: {outcome}")

    def assert_deploy_allowed(self, provenance: ContentProvenance) -> None:
        """
        Call immediately before any publish to owned channels. Hard stop if the
        content still carries the prospect's brand identity.
        """
        if provenance.deploy_target is DeployTarget.OWNED_CHANNEL and not provenance.de_identified:
            raise IPGuardrailError(
                f"artifact {provenance.artifact_id!r} (pitch {provenance.pitch_id!r}) "
                "must be de-identified before deploying to owned channels"
            )


class RepurposeAgent(ABC):
    """
    Strips a prospect's brand/trademarks/product specifics and reframes the
    concept as an anonymized capability showcase, then marks provenance
    de_identified=True. Default standard: FULLY genericized unless written
    showcase permission exists (see blueprint §4.2 guardrail).
    """

    @abstractmethod
    def repurpose(self, artifact: dict, provenance: ContentProvenance) -> tuple[dict, ContentProvenance]:
        """Return (reframed_artifact, updated_provenance) with de_identified=True."""
        ...
