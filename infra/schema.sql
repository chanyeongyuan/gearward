-- Gearward agency platform — Postgres schema
-- Blueprint §7 — multi-tenant from day one.
-- Run once on a fresh Postgres instance with pgvector extension enabled.
-- Apply via: psql $DATABASE_URL -f infra/schema.sql

CREATE EXTENSION IF NOT EXISTS pgvector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Tenancy ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS clients (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    service_lines   TEXT[] NOT NULL,        -- {sales_training, content, migration}
    hubspot_portal  TEXT,                   -- OAuth-linked portal id (client's portal)
    monthly_budget  NUMERIC,                -- USD cap enforced via LiteLLM
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ── Agent runs ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id    UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    module       TEXT NOT NULL CHECK (module IN ('sales_training', 'content', 'migration')),
    agent_name   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'done', 'failed')),
    input        JSONB,
    output       JSONB,
    token_cost   NUMERIC,                   -- pulled from LiteLLM response
    model_tier   INT CHECK (model_tier BETWEEN 0 AND 3),
    trace_id     TEXT,                      -- Langfuse trace id
    error        TEXT,
    created_at   TIMESTAMPTZ DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS agent_runs_client_id_idx ON agent_runs (client_id);
CREATE INDEX IF NOT EXISTS agent_runs_status_idx    ON agent_runs (status);

-- ── Reusable cached artifacts ─────────────────────────────────────────────────
-- product_intelligence per SKU, brand profiles, field mappings — compute once,
-- store here, reuse. Regenerating unchanged artifacts is pure waste.

CREATE TABLE IF NOT EXISTS artifacts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id    UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,             -- product_intelligence|brand_profile|field_map
    key          TEXT NOT NULL,             -- e.g. sku_id, "global", salesforce_object
    data         JSONB NOT NULL,
    valid_until  TIMESTAMPTZ,               -- NULL = valid until inputs change
    created_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (client_id, kind, key)
);

CREATE INDEX IF NOT EXISTS artifacts_client_kind_idx ON artifacts (client_id, kind);

-- ── Memory / RAG ──────────────────────────────────────────────────────────────
-- One DB, no separate vector bill. Hybrid: FTS + pgvector cosine.

CREATE TABLE IF NOT EXISTS memory (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id    UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    module       TEXT NOT NULL CHECK (module IN ('sales_training', 'content', 'migration')),
    content      TEXT NOT NULL,
    embedding    VECTOR(1536),              -- Tier-0 embedder via LiteLLM
    metadata     JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- Vector index (cosine similarity)
CREATE INDEX IF NOT EXISTS memory_embedding_idx
    ON memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Full-text search index
CREATE INDEX IF NOT EXISTS memory_fts_idx
    ON memory USING GIN (to_tsvector('english', content));

-- Tenant filter (always applied before vector ops)
CREATE INDEX IF NOT EXISTS memory_client_module_idx ON memory (client_id, module);

-- ── Content provenance ────────────────────────────────────────────────────────
-- Tracks every generated artifact through the two-flow lifecycle (§4.2).

CREATE TABLE IF NOT EXISTS content_artifacts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id        UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    origin           TEXT NOT NULL CHECK (origin IN ('prospect', 'client')),
    deploy_target    TEXT CHECK (deploy_target IN ('prospect', 'client_channel', 'owned_channel')),
    pitch_id         TEXT,                  -- internal pitch id (Flow 1)
    hubspot_deal_id  TEXT,                  -- deal in YOUR agency HubSpot
    de_identified    BOOLEAN NOT NULL DEFAULT FALSE,
    content          JSONB NOT NULL,        -- copy/image/video urls + metadata
    eval_result      JSONB,                 -- latest EvalResult for this artifact
    engagement       JSONB,                 -- {views, watch_time, shares} from owned channels
    published_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS content_artifacts_client_idx  ON content_artifacts (client_id);
CREATE INDEX IF NOT EXISTS content_artifacts_origin_idx  ON content_artifacts (origin);
CREATE INDEX IF NOT EXISTS content_artifacts_pitch_idx   ON content_artifacts (pitch_id)
    WHERE pitch_id IS NOT NULL;

-- ── Eval golden sets ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS eval_golden_cases (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id    UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    module       TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    input        JSONB NOT NULL,
    expected     JSONB NOT NULL,            -- rubric / reference output
    tags         TEXT[] DEFAULT '{}',
    source       TEXT DEFAULT 'manual'      -- 'manual' | 'auto_curated' (from prod failures)
                    CHECK (source IN ('manual', 'auto_curated')),
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS golden_cases_client_module_idx
    ON eval_golden_cases (client_id, module);
