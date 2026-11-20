"""Knowledge review, approval and export services."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.compat import Jsonb, decode_json_value
from app.db.databricks import DatabricksConnection
from app.services.audit import record_audit

_STATUS_TABLES = {
    "workflow": "workflows",
    "entity": "knowledge_entities",
    "relationship": "knowledge_relationships",
    "step": "workflow_steps",
    "visual": "visual_references",
    "rule": "knowledge_rules",
}

_JSON_FIELDS = (
    "attributes",
    "metadata",
    "prerequisites",
    "warnings",
    "exceptions",
    "conditions",
    "visual_callouts",
    "bounding_box",
)


def _decode_row(row: Any, *fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in fields:
        if field in item:
            item[field] = decode_json_value(item[field])
    return item


def _decode_rows(rows: Any, *fields: str) -> list[dict[str, Any]]:
    return [item for row in rows if (item := _decode_row(row, *fields)) is not None]


async def knowledge_summary(
    conn: DatabricksConnection, *, version_id: UUID | None = None
) -> dict[str, Any]:
    where = "WHERE document_version_id = %s" if version_id else ""
    params = (str(version_id),) if version_id else ()
    counts: dict[str, int] = {}
    for key, table in (
        ("entities", "knowledge_entities"),
        ("relationships", "knowledge_relationships"),
        ("workflows", "workflows"),
        ("steps", "workflow_steps"),
        ("visuals", "visual_references"),
        ("rules", "knowledge_rules"),
        ("page_markdown", "page_markdown"),
    ):
        row = await (
            await conn.execute(f"SELECT count(*) AS n FROM {table} {where}", params)
        ).fetchone()
        counts[key] = int(row["n"])
    conflict_where = "WHERE status = 'open'" if not version_id else (
        "WHERE status = 'open' AND document_id = "
        "(SELECT document_id FROM document_versions WHERE id = %s)"
    )
    row = await (
        await conn.execute(
            f"SELECT count(*) AS n FROM knowledge_conflicts {conflict_where}", params
        )
    ).fetchone()
    counts["open_conflicts"] = int(row["n"])
    by_status = await (
        await conn.execute(
            f"""
            SELECT status, count(*) AS n FROM workflows {where}
            GROUP BY status ORDER BY status
            """,
            params,
        )
    ).fetchall()
    counts["workflows_by_status"] = {
        row["status"]: int(row["n"]) for row in by_status
    }
    return counts


async def list_workflows(
    conn: DatabricksConnection,
    *,
    version_id: UUID | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if version_id:
        clauses.append("w.document_version_id = %s")
        params.append(str(version_id))
    if status:
        clauses.append("w.status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = await (
        await conn.execute(
            f"""
            SELECT w.id, w.document_id, w.document_version_id, w.workflow_key,
                   w.title, w.summary, w.content_type, w.confidence, w.status,
                   w.source_page_start, w.source_page_end, w.created_at,
                   d.title AS document_title, d.filename,
                   coalesce(step_counts.step_count, 0) AS step_count,
                   coalesce(visual_counts.visual_count, 0) AS visual_count,
                   coalesce(rule_counts.rule_count, 0) AS rule_count
            FROM workflows w
            JOIN documents d ON d.id = w.document_id
            LEFT JOIN (
                SELECT workflow_id, count(*) AS step_count
                FROM workflow_steps
                GROUP BY workflow_id
            ) step_counts ON step_counts.workflow_id = w.id
            LEFT JOIN (
                SELECT s.workflow_id, count(v.id) AS visual_count
                FROM workflow_steps s
                LEFT JOIN visual_references v ON v.workflow_step_id = s.id
                GROUP BY s.workflow_id
            ) visual_counts ON visual_counts.workflow_id = w.id
            LEFT JOIN (
                SELECT workflow_id, count(*) AS rule_count
                FROM knowledge_rules
                GROUP BY workflow_id
            ) rule_counts ON rule_counts.workflow_id = w.id
            {where}
            ORDER BY w.created_at DESC, w.title
            LIMIT %s
            """,
            tuple(params),
        )
    ).fetchall()
    return _decode_rows(rows, *_JSON_FIELDS)


async def workflow_detail(
    conn: DatabricksConnection, workflow_id: UUID
) -> dict[str, Any] | None:
    workflow = await (
        await conn.execute(
            """
            SELECT w.*, d.title AS document_title, d.filename
            FROM workflows w
            JOIN documents d ON d.id = w.document_id
            WHERE w.id = %s
            """,
            (str(workflow_id),),
        )
    ).fetchone()
    if workflow is None:
        return None
    steps = await (
        await conn.execute(
            """
            SELECT s.*, p.page_image_path
            FROM workflow_steps s
            LEFT JOIN document_pages p ON p.id = s.document_page_id
            WHERE s.workflow_id = %s
            ORDER BY s.step_number
            """,
            (str(workflow_id),),
        )
    ).fetchall()
    visuals = await (
        await conn.execute(
            """
            SELECT * FROM visual_references
            WHERE workflow_step_id IN
                (SELECT id FROM workflow_steps WHERE workflow_id = %s)
            ORDER BY page_number, callout_number
            """,
            (str(workflow_id),),
        )
    ).fetchall()
    rules = await (
        await conn.execute(
            "SELECT * FROM knowledge_rules WHERE workflow_id = %s "
            "ORDER BY rule_type, source_page_number",
            (str(workflow_id),),
        )
    ).fetchall()
    sources = await (
        await conn.execute(
            """
            SELECT * FROM knowledge_sources
            WHERE knowledge_type = 'workflow' AND knowledge_id = %s
            ORDER BY page_number
            """,
            (str(workflow_id),),
        )
    ).fetchall()
    return {
        "workflow": _decode_row(workflow, *_JSON_FIELDS),
        "steps": _decode_rows(steps, *_JSON_FIELDS),
        "visuals": _decode_rows(visuals, *_JSON_FIELDS),
        "rules": _decode_rows(rules, *_JSON_FIELDS),
        "sources": _decode_rows(sources, *_JSON_FIELDS),
    }


async def set_knowledge_status(
    conn: DatabricksConnection,
    *,
    knowledge_type: str,
    knowledge_id: UUID,
    status: str,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    if status not in {"proposed", "approved", "rejected", "superseded"}:
        raise ValueError("Invalid knowledge status")
    table = _STATUS_TABLES.get(knowledge_type)
    if table is None:
        raise ValueError("Invalid knowledge type")

    async with conn.transaction():
        if knowledge_type == "workflow":
            await conn.execute(
                f"UPDATE {table} SET status = %s, approved_at = "
                "CASE WHEN %s = 'approved' THEN current_timestamp() ELSE approved_at END, "
                "approved_by = CASE WHEN %s = 'approved' THEN %s ELSE approved_by END "
                "WHERE id = %s",
                (status, status, status, str(actor_user_id) if actor_user_id else None, str(knowledge_id)),
            )
            await conn.execute(
                "UPDATE workflow_steps SET status = %s WHERE workflow_id = %s",
                (status, str(knowledge_id)),
            )
            await conn.execute(
                "UPDATE knowledge_rules SET status = %s WHERE workflow_id = %s",
                (status, str(knowledge_id)),
            )
            await conn.execute(
                "UPDATE visual_references SET status = %s WHERE workflow_step_id IN "
                "(SELECT id FROM workflow_steps WHERE workflow_id = %s)",
                (status, str(knowledge_id)),
            )
        else:
            await conn.execute(
                f"UPDATE {table} SET status = %s WHERE id = %s",
                (status, str(knowledge_id)),
            )
        row = await (
            await conn.execute(f"SELECT * FROM {table} WHERE id = %s", (str(knowledge_id),))
        ).fetchone()
        if row is None:
            raise ValueError("Knowledge item was not found")
        decoded = _decode_row(row, *_JSON_FIELDS)
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action=f"knowledge.{knowledge_type}.{status}",
            entity_type=knowledge_type,
            entity_id=str(knowledge_id),
        )
    return decoded


async def approve_version_knowledge(
    conn: DatabricksConnection,
    version_id: UUID,
    *,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    async with conn.transaction():
        workflows = await (
            await conn.execute(
                "SELECT id FROM workflows WHERE document_version_id = %s",
                (str(version_id),),
            )
        ).fetchall()
        await conn.execute(
            "UPDATE workflows SET status = 'approved', approved_at = current_timestamp(), "
            "approved_by = %s WHERE document_version_id = %s",
            (str(actor_user_id) if actor_user_id else None, str(version_id)),
        )
        await conn.execute(
            "UPDATE workflow_steps SET status = 'approved' WHERE workflow_id IN "
            "(SELECT id FROM workflows WHERE document_version_id = %s)",
            (str(version_id),),
        )
        await conn.execute(
            "UPDATE knowledge_entities SET status = 'approved', approved_at = current_timestamp(), "
            "approved_by = %s WHERE document_version_id = %s",
            (str(actor_user_id) if actor_user_id else None, str(version_id)),
        )
        await conn.execute(
            "UPDATE knowledge_relationships SET status = 'approved' "
            "WHERE document_version_id = %s",
            (str(version_id),),
        )
        await conn.execute(
            "UPDATE knowledge_rules SET status = 'approved' WHERE document_version_id = %s",
            (str(version_id),),
        )
        await conn.execute(
            "UPDATE visual_references SET status = 'approved' WHERE document_version_id = %s",
            (str(version_id),),
        )
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action="knowledge.version_approved",
            entity_type="document_version",
            entity_id=str(version_id),
            metadata={"workflows": len(workflows)},
        )
    return {"version_id": str(version_id), "workflows": len(workflows)}


async def list_conflicts(
    conn: DatabricksConnection, *, status: str = "open", limit: int = 100
) -> list[dict[str, Any]]:
    rows = await (
        await conn.execute(
            """
            SELECT c.*, d.title AS document_title
            FROM knowledge_conflicts c
            LEFT JOIN documents d ON d.id = c.document_id
            WHERE c.status = %s
            ORDER BY c.created_at DESC
            LIMIT %s
            """,
            (status, limit),
        )
    ).fetchall()
    return _decode_rows(rows, *_JSON_FIELDS)


async def resolve_conflict(
    conn: DatabricksConnection,
    conflict_id: UUID,
    *,
    status: str,
    resolution: str,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    if status not in {"resolved", "ignored", "open"}:
        raise ValueError("Invalid conflict status")
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE knowledge_conflicts
            SET status = %s, resolution = %s,
                resolved_at = CASE WHEN %s = 'open' THEN NULL ELSE current_timestamp() END,
                resolved_by = %s
            WHERE id = %s
            """,
            (
                status,
                resolution,
                status,
                str(actor_user_id) if actor_user_id else None,
                str(conflict_id),
            ),
        )
        row = await (
            await conn.execute(
                "SELECT * FROM knowledge_conflicts WHERE id = %s", (str(conflict_id),)
            )
        ).fetchone()
        if row is None:
            raise ValueError("Conflict was not found")
        decoded = _decode_row(row, *_JSON_FIELDS)
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action=f"knowledge.conflict_{status}",
            entity_type="knowledge_conflict",
            entity_id=str(conflict_id),
            metadata={"resolution": resolution},
        )
    return decoded


async def export_workflow_markdown(
    conn: DatabricksConnection, workflow_id: UUID
) -> str | None:
    detail = await workflow_detail(conn, workflow_id)
    if detail is None:
        return None
    workflow = detail["workflow"]
    lines = [f"# {workflow['title']}", ""]
    if workflow.get("summary"):
        lines.extend([str(workflow["summary"]), ""])
    lines.extend(
        [
            f"- Status: {workflow['status']}",
            f"- Source pages: {workflow.get('source_page_start')} - "
            f"{workflow.get('source_page_end')}",
            f"- Document: {workflow.get('document_title')}",
            "",
        ]
    )
    if detail["steps"]:
        lines.append("## Steps")
        for step in detail["steps"]:
            lines.append(f"{step['step_number']}. {step['action']}")
            if step.get("description"):
                lines.append(f"   {step['description']}")
            for value in _json_list(step.get("conditions")):
                lines.append(f"   - Condition: {value}")
            for value in _json_list(step.get("warnings")):
                lines.append(f"   - Warning: {value}")
            for value in _json_list(step.get("exceptions")):
                lines.append(f"   - Exception: {value}")
        lines.append("")
    if detail["visuals"]:
        lines.append("## Visual references")
        for visual in detail["visuals"]:
            callout = f" [{visual['callout_number']}]" if visual.get("callout_number") else ""
            lines.append(
                f"- Page {visual.get('page_number')}: {visual['label']}{callout}"
                + (f" - {visual['description']}" if visual.get("description") else "")
            )
        lines.append("")
    if detail["rules"]:
        lines.append("## Rules")
        for rule in detail["rules"]:
            lines.append(
                f"- {str(rule['rule_type']).replace('_', ' ').title()}: {rule['rule_text']}"
            )
        lines.append("")
    if detail["sources"]:
        lines.append("## Sources")
        for source in detail["sources"]:
            lines.append(
                f"- Page {source.get('page_number')}: {source.get('quote') or ''}"
            )
    return "\n".join(lines).strip() + "\n"


def _json_list(value: Any) -> list[Any]:
    decoded = decode_json_value(value)
    if isinstance(decoded, list):
        return decoded
    if decoded is None:
        return []
    return [decoded]


async def store_manual_knowledge_override(
    conn: DatabricksConnection,
    *,
    workflow_id: UUID,
    actor_user_id: UUID | str | None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with conn.transaction():
        current = await (
            await conn.execute(
                "SELECT id, summary, metadata FROM workflows WHERE id = %s",
                (str(workflow_id),),
            )
        ).fetchone()
        if current is None:
            raise ValueError("Workflow was not found")
        existing_metadata = decode_json_value(current.get("metadata"))
        merged_metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
        merged_metadata.update(metadata or {})
        await conn.execute(
            """
            UPDATE workflows
            SET summary = coalesce(%s, summary), metadata = %s
            WHERE id = %s
            """,
            (summary, Jsonb(merged_metadata), str(workflow_id)),
        )
        row = await (
            await conn.execute("SELECT * FROM workflows WHERE id = %s", (str(workflow_id),))
        ).fetchone()
        decoded = _decode_row(row, *_JSON_FIELDS)
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action="knowledge.workflow_corrected",
            entity_type="workflow",
            entity_id=str(workflow_id),
        )
    return decoded
