# core/ — platform contracts (Phase 0)

These are the **seams** the whole agency platform is built against. They implement
the day-1 structure from `agency-architecture-blueprint.md`: get the interfaces
right first, default to cheap implementations, swap freely later.

## The one rule
Service modules and agents depend on the **abstract** contracts here — never on a
concrete backend. Wire concrete implementations once at startup and inject them.
This is what makes memory, harness, eval, and deployment swappable without a rewrite.

## The four contracts
| File | Contract | What it isolates |
|------|----------|------------------|
| `memory.py` | `MemoryService` | Cross-session, per-tenant memory (API Memory Tool + Postgres). Swap backend without touching agents. |
| `harness.py` | `Harness` + `select_harness()` | Where an agent runs: self-hosted LangGraph (default) vs Managed Agents. Chosen per module by `WorkloadProfile`. |
| `evaluator.py` | `Evaluator` | The learning layer: offline golden sets, online scoring, CI gate, engagement ground-truth. |
| `provenance.py` | `DeploymentRouter` + `RepurposeAgent` | The two-flow branch + the IP de-identification guardrail. |

`types.py` is the shared vocabulary (stdlib-only — never import the other modules into it).

## Using this with Claude Code
1. Drop `core/` and `agency-architecture-blueprint.md` into the repo root.
2. Point Claude Code at the blueprint as the spec and `core/` as the contracts.
3. Build in blueprint order — each `NotImplementedError` / `TODO(claude-code)` is a unit of work:
   - **Phase 0:** these stubs (done) + Postgres schema (blueprint §7) + LiteLLM gateway (§8).
   - **Phase 1:** implement `PostgresMemoryService`; migrate `feedback_log.json` into it.
   - **Phase 2:** implement `LangGraphHarness`; build the content graph (Research → Product Intelligence → Generate → Critic → Novelty → Publish), all wired through `MemoryService` + `DeploymentRouter`.
   - **Phase 3:** implement `LLMJudgeEvaluator` + the CI gate; add the nightly "dreaming-lite" refinement job.
   - **Phase 4:** implement `ManagedAgentsHarness`; bring Sales Training + Migration onto the core.

## Guardrails to keep green
- **Tenancy:** every memory read/write and agent run is filtered by `client_id` at the data layer. A cross-tenant leak is a P0.
- **IP:** `DeploymentRouter.assert_deploy_allowed()` must run before any owned-channel publish. Never post a prospect's branded content un-de-identified.
- **Cost:** route via LiteLLM starting at the lowest tier; never send batchable work to the Managed Agents harness (no Batch API there).

> Version-sensitive at build time: model strings, the Memory Tool type
> (`memory_20250818`), and the Managed Agents beta header
> (`managed-agents-2026-04-01`). Verify against current Anthropic docs.
