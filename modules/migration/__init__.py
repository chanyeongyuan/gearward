"""
modules.migration — CRM Migration module (blueprint §4.3).

Maps CLIENT Salesforce → CLIENT HubSpot. Read-only on Salesforce, write to
the client's new HubSpot portal. Never touches your own agency portal.
High record volume, simple per-record work → cheapest tier + batch processing.
"""
from .graph import build_migration_graph, MigrationState

__all__ = ["build_migration_graph", "MigrationState"]
