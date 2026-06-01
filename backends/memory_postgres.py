"""
backends.memory_postgres — PostgresMemoryService.

Hybrid retrieval: reciprocal-rank fusion of Postgres FTS + pgvector cosine.
Hard-filtered by client_id at SQL level — tenant isolation is never left to
application code.

Dependencies: psycopg[binary], pgvector
Schema: infra/schema.sql (memory table + ivfflat + GIN FTS indexes)
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import psycopg
from pgvector.psycopg import register_vector

from core import ClientId, MemoryRecord, MemoryService, Module


class PostgresMemoryService(MemoryService):
    """
    Postgres + pgvector backend.

    Args:
        db_url:   libpq connection string — use DATABASE_URL from env.
        embedder: callable(text: str) -> list[float]; use the LiteLLM
                  /embeddings endpoint (model=embedder, Tier-0 cost).
    """

    def __init__(self, *, db_url: str, embedder: Callable[[str], list[float]]) -> None:
        self._db_url = db_url
        self._embedder = embedder

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _conn(self) -> psycopg.Connection:
        conn = psycopg.connect(self._db_url)
        register_vector(conn)
        return conn

    @staticmethod
    def _row_to_record(row: tuple) -> MemoryRecord:
        record_id, client_id, module, content, metadata, created_at, score = row
        return MemoryRecord(
            client_id=client_id,
            module=Module(module),
            content=content,
            metadata=metadata or {},
            record_id=str(record_id),
            score=float(score) if score is not None else None,
            created_at=created_at,
        )

    # ── MemoryService interface ───────────────────────────────────────────────

    def write(self, record: MemoryRecord) -> str:
        record_id = str(uuid.uuid4())
        embedding = self._embedder(record.content)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO memory (id, client_id, module, content, embedding, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s::vector, %s, %s)
                """,
                (
                    record_id,
                    record.client_id,
                    record.module.value,
                    record.content,
                    embedding,
                    json.dumps(record.metadata),
                    record.created_at or datetime.now(timezone.utc),
                ),
            )
        return record_id

    def read(
        self, client_id: ClientId, module: Module, query: str, k: int = 5
    ) -> list[MemoryRecord]:
        """Hybrid FTS + pgvector with reciprocal-rank fusion (RRF k=60)."""
        embedding = self._embedder(query)
        with self._conn() as conn:
            rows = conn.execute(
                """
                WITH semantic AS (
                    SELECT id,
                           ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rn
                    FROM memory
                    WHERE client_id = %s AND module = %s
                ),
                keyword AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               ORDER BY ts_rank(
                                   to_tsvector('english', content),
                                   plainto_tsquery('english', %s)
                               ) DESC
                           ) AS rn
                    FROM memory
                    WHERE client_id = %s AND module = %s
                      AND to_tsvector('english', content) @@ plainto_tsquery('english', %s)
                ),
                rrf AS (
                    SELECT COALESCE(s.id, k.id) AS id,
                           (1.0 / (60 + COALESCE(s.rn, 1000)) +
                            1.0 / (60 + COALESCE(k.rn, 1000))) AS rrf_score
                    FROM semantic s
                    FULL OUTER JOIN keyword k USING (id)
                )
                SELECT m.id, m.client_id, m.module, m.content, m.metadata,
                       m.created_at, r.rrf_score
                FROM rrf r
                JOIN memory m ON m.id = r.id
                ORDER BY r.rrf_score DESC
                LIMIT %s
                """,
                (
                    embedding, client_id, module.value,
                    query, client_id, module.value, query,
                    k,
                ),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def search(self, client_id: ClientId, query: str, k: int = 5) -> list[MemoryRecord]:
        """Semantic search across all modules for one tenant (used by refinement job)."""
        embedding = self._embedder(query)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, client_id, module, content, metadata, created_at,
                       1 - (embedding <=> %s::vector) AS score
                FROM memory
                WHERE client_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (embedding, client_id, embedding, k),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def forget(self, client_id: ClientId, record_id: str) -> bool:
        with self._conn() as conn:
            result = conn.execute(
                "DELETE FROM memory WHERE id = %s AND client_id = %s",
                (record_id, client_id),
            )
        return result.rowcount > 0
