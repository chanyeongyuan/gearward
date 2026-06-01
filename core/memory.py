"""
core.memory — the MemoryService seam.

Real cross-session memory on YOUR OWN stack (API Memory Tool + Postgres),
not Managed Agents. Model-agnostic, per-tenant, ~2,500 tokens overhead, no
surcharge. Every agent reads/writes memory only through this interface, so the
backend can be swapped (Postgres -> Managed Agents memory -> Mem0) without
touching agent logic.

Design rules:
  * Extractive, not dump-everything. write() should be called with content the
    agent judged worth keeping — not every turn.
  * Per-tenant isolation is enforced here: a read for client A must NEVER return
    client B's records. Treat a cross-tenant leak as a P0 bug.
  * Retrieval is hybrid: keyword (Postgres FTS) + semantic (pgvector cosine).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .types import ClientId, MemoryRecord, Module


class MemoryService(ABC):
    """Implement one concrete subclass per backend. Modules depend on THIS."""

    @abstractmethod
    def write(self, record: MemoryRecord) -> str:
        """
        Persist a memory and return its record_id.
        MUST stamp client_id + module on the stored row. Embed `content` for
        semantic search on write.
        """
        ...

    @abstractmethod
    def read(self, client_id: ClientId, module: Module, query: str, k: int = 5) -> list[MemoryRecord]:
        """
        Hybrid retrieval of the k most relevant memories for this client+module.
        Combine keyword (FTS) and semantic (vector) results; return ranked,
        de-duplicated records with `score` populated. Filter by client_id at the
        SQL level — never in application code after the fact.
        """
        ...

    @abstractmethod
    def search(self, client_id: ClientId, query: str, k: int = 5) -> list[MemoryRecord]:
        """Cross-module search within a single tenant (e.g. for the refinement job)."""
        ...

    @abstractmethod
    def forget(self, client_id: ClientId, record_id: str) -> bool:
        """Delete one record. Returns True if a row was removed."""
        ...


