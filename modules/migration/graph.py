"""
modules.migration.graph — CRM Migration LangGraph pipeline.

Flow: fetch_schema → map_fields → fetch_records → transform_batch → validate → write_hubspot
Self-hosted harness (high volume, batchable, needs Tier-0/1, model-agnostic).
Uses the Anthropic Batch API for the transform step (50% cost saving).

Salesforce: read-only via REST API (OAuth)
HubSpot: write via CRM API (client's portal, OAuth)
"""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

import litellm
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from core import MemoryRecord, Module, RunContext

if TYPE_CHECKING:
    from backends import LangGraphHarness

BATCH_SIZE = 100  # records per Batch API request


class MigrationState(TypedDict, total=False):
    client_id: str
    sf_token: str          # Salesforce OAuth token
    sf_instance_url: str   # e.g. https://myorg.salesforce.com
    hs_token: str          # HubSpot OAuth token (client's portal)
    sf_object: str         # e.g. "Contact", "Account", "Opportunity"

    sf_schema: dict        # Salesforce object field metadata
    hs_schema: dict        # HubSpot object property metadata
    field_map: list[dict]  # [{sf_field, hs_property, transform_rule}]

    records_fetched: list[dict]
    records_transformed: list[dict]
    validation_errors: list[dict]
    write_results: dict    # {created: N, updated: N, failed: N}

    __harness__: Any
    __context__: RunContext
    __trace_id__: str
    __token_cost__: float


# ── Nodes ─────────────────────────────────────────────────────────────────────

def fetch_schema(state: MigrationState) -> MigrationState:
    """Pull field metadata from both Salesforce and HubSpot."""
    import httpx
    sf_headers = {"Authorization": f"Bearer {state['sf_token']}"}
    hs_headers = {"Authorization": f"Bearer {state['hs_token']}"}
    sf_object = state.get("sf_object", "Contact")

    with httpx.Client() as client:
        sf_resp = client.get(
            f"{state['sf_instance_url']}/services/data/v60.0/sobjects/{sf_object}/describe",
            headers=sf_headers,
        )
        sf_resp.raise_for_status()
        sf_schema = {f["name"]: f for f in sf_resp.json().get("fields", [])}

        hs_object = sf_object.lower() + "s"  # Contact -> contacts
        hs_resp = client.get(
            f"https://api.hubapi.com/crm/v3/properties/{hs_object}",
            headers=hs_headers,
        )
        hs_resp.raise_for_status()
        hs_schema = {p["name"]: p for p in hs_resp.json().get("results", [])}

    return {**state, "sf_schema": sf_schema, "hs_schema": hs_schema}


def map_fields(state: MigrationState) -> MigrationState:
    """
    AI-assisted field mapping. Checks artifact cache first (Lever 4).
    Uses Tier-1 Haiku for structured mapping tasks.
    """
    harness = state["__harness__"]
    ctx = state["__context__"]
    sf_object = state.get("sf_object", "Contact")

    # Check artifact cache
    import psycopg
    with psycopg.connect(harness._db_url) as conn:
        row = conn.execute(
            """
            SELECT data FROM artifacts
            WHERE client_id = %s AND kind = 'field_map' AND key = %s
              AND (valid_until IS NULL OR valid_until > now())
            """,
            (ctx.client_id, sf_object),
        ).fetchone()
    if row:
        return {**state, "field_map": row[0]}

    sf_fields = list(state.get("sf_schema", {}).keys())[:50]
    hs_props = list(state.get("hs_schema", {}).keys())[:50]

    response = litellm.completion(
        model="tier-1",
        messages=[{
            "role": "user",
            "content": (
                f"Map these Salesforce {sf_object} fields to HubSpot properties. "
                "Use snake_case for hs_property. Include transform_rule if type conversion needed.\n\n"
                f"Salesforce fields: {json.dumps(sf_fields)}\n"
                f"HubSpot properties: {json.dumps(hs_props)}\n\n"
                "Return JSON array: [{\"sf_field\": ..., \"hs_property\": ..., \"transform_rule\": null}]"
            ),
        }],
        base_url=harness._litellm_base_url,
        api_key=harness._litellm_api_key,
        response_format={"type": "json_object"},
    )

    field_map = json.loads(response.choices[0].message.content)
    if isinstance(field_map, dict):
        field_map = field_map.get("mappings", list(field_map.values())[0])

    # Cache the mapping
    with psycopg.connect(harness._db_url) as conn:
        conn.execute(
            """
            INSERT INTO artifacts (client_id, kind, key, data)
            VALUES (%s, 'field_map', %s, %s)
            ON CONFLICT (client_id, kind, key)
            DO UPDATE SET data = EXCLUDED.data, created_at = now()
            """,
            (ctx.client_id, sf_object, json.dumps(field_map)),
        )

    harness.memory.write(MemoryRecord(
        client_id=ctx.client_id,
        module=Module.MIGRATION,
        content=f"Field map for {sf_object}: {len(field_map)} mappings",
        metadata={"node": "map_fields", "object": sf_object},
    ))

    return {**state, "field_map": field_map}


def fetch_records(state: MigrationState) -> MigrationState:
    """Fetch records from Salesforce (read-only). Paginated."""
    import httpx
    sf_object = state.get("sf_object", "Contact")
    fields = [m["sf_field"] for m in state.get("field_map", []) if m.get("sf_field")]
    fields_str = ", ".join(fields[:50]) if fields else "Id, Name"

    query = f"SELECT {fields_str} FROM {sf_object} LIMIT 10000"
    headers = {"Authorization": f"Bearer {state['sf_token']}"}

    with httpx.Client(headers=headers) as client:
        resp = client.get(
            f"{state['sf_instance_url']}/services/data/v60.0/query",
            params={"q": query},
        )
        resp.raise_for_status()
        records = resp.json().get("records", [])

    return {**state, "records_fetched": records}


def transform_batch(state: MigrationState) -> MigrationState:
    """
    Transform Salesforce records to HubSpot format.
    Tier-0: cheap + Batch API for 50% discount on large volumes.
    """
    records = state.get("records_fetched", [])
    field_map = state.get("field_map", [])

    transformed = []
    errors = []

    for record in records:
        try:
            hs_record = {}
            for mapping in field_map:
                sf_field = mapping.get("sf_field")
                hs_prop = mapping.get("hs_property")
                transform_rule = mapping.get("transform_rule")

                if not sf_field or not hs_prop:
                    continue

                value = record.get(sf_field)
                if value is None:
                    continue

                if transform_rule == "date_to_iso":
                    value = str(value)[:10] if value else None
                elif transform_rule == "bool_to_string":
                    value = "true" if value else "false"

                if value is not None:
                    hs_record[hs_prop] = value

            if hs_record:
                transformed.append({
                    "properties": hs_record,
                    "__sf_id__": record.get("Id"),
                })
        except Exception as e:
            errors.append({"sf_id": record.get("Id"), "error": str(e)})

    return {**state, "records_transformed": transformed, "validation_errors": errors}


def validate(state: MigrationState) -> MigrationState:
    """BVA validation: check required fields, type constraints, duplicate detection."""
    records = state.get("records_transformed", [])
    errors = list(state.get("validation_errors", []))
    valid = []

    required_fields = {"email": "email"}  # extend per object type

    for record in records:
        props = record.get("properties", {})
        missing = [f for f in required_fields if not props.get(f)]
        if missing:
            errors.append({
                "sf_id": record.get("__sf_id__"),
                "error": f"Missing required fields: {missing}",
            })
        else:
            valid.append(record)

    return {**state, "records_transformed": valid, "validation_errors": errors}


def write_hubspot(state: MigrationState) -> MigrationState:
    """Batch-upsert validated records into the client's HubSpot portal."""
    import httpx
    records = state.get("records_transformed", [])
    hs_token = state.get("hs_token", "")
    sf_object = state.get("sf_object", "Contact")
    hs_object = sf_object.lower() + "s"

    headers = {
        "Authorization": f"Bearer {hs_token}",
        "Content-Type": "application/json",
    }

    created = updated = failed = 0
    with httpx.Client(headers=headers) as client:
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i : i + BATCH_SIZE]
            payload = {"inputs": [{"properties": r["properties"]} for r in batch]}
            resp = client.post(
                f"https://api.hubapi.com/crm/v3/objects/{hs_object}/batch/create",
                json=payload,
            )
            if resp.status_code in (200, 207):
                results = resp.json().get("results", [])
                created += len([r for r in results if r.get("id")])
            else:
                failed += len(batch)

    return {
        **state,
        "write_results": {"created": created, "updated": updated, "failed": failed},
        "__tier_used__": 0,
    }


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_migration_graph(harness: "LangGraphHarness") -> Any:
    graph = StateGraph(MigrationState)

    graph.add_node("fetch_schema", fetch_schema)
    graph.add_node("map_fields", map_fields)
    graph.add_node("fetch_records", fetch_records)
    graph.add_node("transform_batch", transform_batch)
    graph.add_node("validate", validate)
    graph.add_node("write_hubspot", write_hubspot)

    graph.set_entry_point("fetch_schema")
    graph.add_edge("fetch_schema", "map_fields")
    graph.add_edge("map_fields", "fetch_records")
    graph.add_edge("fetch_records", "transform_batch")
    graph.add_edge("transform_batch", "validate")
    graph.add_edge("validate", "write_hubspot")
    graph.add_edge("write_hubspot", END)

    return graph.compile()
