"""Deterministic Phase 3 knowledge organization.

Vision output is normalized into reviewable entities, workflows, steps, visual
references, rules, sources and conflicts. Source pages and chunks remain the
evidence of record; this module never replaces them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from uuid import UUID, uuid4

from app.db.compat import Jsonb, decode_json_value
from app.db.databricks import DatabricksConnection
from app.utils.hashing import sha256_text

_NUMBERED_STEP_RE = re.compile(
    r"^\s*(?:step\s*)?(\d{1,3})[.)-]\s+(.+?)\s*$", re.IGNORECASE
)
_TIMESTAMP_RE = re.compile(r"\(\d{1,2}:\d{2}(?:\s*-\s*\d{1,2}:\d{2})?\)")


async def rebuild_version_knowledge(
    conn: DatabricksConnection,
    version_id: UUID,
    *,
    mark_approved: bool = False,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    version = await (
        await conn.execute(
            """
            SELECT dv.id, dv.document_id, dv.version_number, d.title, d.filename,
                   d.document_type
            FROM document_versions dv
            JOIN documents d ON d.id = dv.document_id
            WHERE dv.id = %s
            """,
            (str(version_id),),
        )
    ).fetchone()
    if version is None:
        raise ValueError("Document version was not found")

    page_rows = await (
        await conn.execute(
            """
            SELECT id, physical_page_number, page_text, content_type, layout,
                   analysis, confidence, page_image_path
            FROM document_pages
            WHERE document_version_id = %s
            ORDER BY physical_page_number
            """,
            (str(version_id),),
        )
    ).fetchall()
    pages = [dict(page) for page in page_rows]
    for page in pages:
        page["layout"] = decode_json_value(page.get("layout")) or {}
        page["analysis"] = decode_json_value(page.get("analysis")) or {}

    status = "approved" if mark_approved else "proposed"
    approved_sql = "current_timestamp()" if mark_approved else "NULL"
    approved_by_sql = "%s" if mark_approved and actor_user_id else "NULL"

    stats = {
        "entities": 0,
        "relationships": 0,
        "workflows": 0,
        "steps": 0,
        "visuals": 0,
        "rules": 0,
        "sources": 0,
        "conflicts": 0,
        "page_markdown": 0,
    }

    async with conn.transaction():
        await conn.execute(
            "DELETE FROM knowledge_sources WHERE document_version_id = %s",
            (str(version_id),),
        )
        await conn.execute(
            "DELETE FROM knowledge_conflicts "
            "WHERE document_id = %s AND detected_by = 'system' AND status = 'open'",
            (str(version["document_id"]),),
        )
        await conn.execute(
            "DELETE FROM workflow_steps WHERE workflow_id IN "
            "(SELECT id FROM workflows WHERE document_version_id = %s)",
            (str(version_id),),
        )
        for table in (
            "visual_references",
            "knowledge_rules",
            "knowledge_relationships",
            "knowledge_entities",
            "workflows",
            "page_markdown",
        ):
            await conn.execute(
                f"DELETE FROM {table} WHERE document_version_id = %s",
                (str(version_id),),
            )

        entity_ids: dict[tuple[str, str], UUID] = {}
        entity_pages: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        for page in pages:
            analysis = page.get("analysis") or {}
            for item in analysis.get("domain_knowledge") or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                entity_type = str(item.get("entity_type") or "concept").strip()
                normalized = _normalize(name)
                key = (entity_type.lower(), normalized)
                if key in entity_ids:
                    entity_id = entity_ids[key]
                else:
                    entity_id = uuid4()
                    entity_ids[key] = entity_id
                    await conn.execute(
                        f"""
                        INSERT INTO knowledge_entities
                            (id, document_id, document_version_id, entity_type, name,
                             normalized_name, description, attributes, metadata,
                             confidence, status, source_page_start, source_page_end,
                             approved_at, approved_by, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                {approved_sql}, {approved_by_sql}, current_timestamp(),
                                current_timestamp())
                        """,
                        (
                            str(entity_id),
                            str(version["document_id"]),
                            str(version_id),
                            entity_type,
                            name[:500],
                            normalized[:500],
                            str(item.get("description") or "")[:8000],
                            Jsonb(item.get("attributes") or {}),
                            Jsonb({}),
                            _confidence(analysis.get("confidence"), 0.6),
                            status,
                            int(page["physical_page_number"]),
                            int(page["physical_page_number"]),
                            *((str(actor_user_id),) if mark_approved and actor_user_id else ()),
                        ),
                    )
                    stats["entities"] += 1
                entity_pages[entity_id].append(page)
                stats["sources"] += 1
                await _insert_source(
                    conn,
                    version,
                    knowledge_type="entity",
                    knowledge_id=entity_id,
                    page=page,
                    quote=name,
                )

        for index, (from_id, from_pages) in enumerate(entity_pages.items()):
            for to_id, to_pages in list(entity_pages.items())[index + 1 :]:
                if from_id == to_id:
                    continue
                shared = _shared_page(from_pages, to_pages)
                if shared is None:
                    continue
                relationship_id = uuid4()
                await conn.execute(
                    """
                    INSERT INTO knowledge_relationships
                        (id, document_id, document_version_id, from_entity_id,
                         to_entity_id, relationship_type, description, confidence,
                         status, source_page_start, source_page_end, metadata,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'co_occurs_with', %s, 0.45, %s, %s, %s,
                            %s, current_timestamp(), current_timestamp())
                    """,
                    (
                        str(relationship_id),
                        str(version["document_id"]),
                        str(version_id),
                        str(from_id),
                        str(to_id),
                        "Entities appear on the same source page",
                        status,
                        int(shared["physical_page_number"]),
                        int(shared["physical_page_number"]),
                        Jsonb({}),
                    ),
                )
                stats["relationships"] += 1

        groups = _workflow_groups(version, pages)
        workflow_records: list[dict[str, Any]] = []
        for group in groups:
            workflow_id = uuid4()
            first_page = group["pages"][0]
            last_page = group["pages"][-1]
            workflow_key = f"{_slug(group['title'])}-{first_page['physical_page_number']}"
            fingerprint = sha256_text(
                "\n".join(step["action"] for step in group["steps"])[:100000]
            )
            await conn.execute(
                f"""
                INSERT INTO workflows
                    (id, document_id, document_version_id, workflow_key, title,
                     normalized_title, summary, content_type, prerequisites,
                     warnings, exceptions, confidence, status, source_page_start,
                     source_page_end, metadata, approved_at, approved_by,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, {approved_sql}, {approved_by_sql},
                        current_timestamp(), current_timestamp())
                """,
                (
                    str(workflow_id),
                    str(version["document_id"]),
                    str(version_id),
                    workflow_key,
                    group["title"][:500],
                    _normalize(group["title"])[:500],
                    group["summary"][:8000],
                    group["content_type"],
                    Jsonb(group["prerequisites"]),
                    Jsonb(group["warnings"]),
                    Jsonb(group["exceptions"]),
                    group["confidence"],
                    status,
                    int(first_page["physical_page_number"]),
                    int(last_page["physical_page_number"]),
                    Jsonb(
                        {
                            "step_fingerprint": fingerprint,
                            "heading": group["heading"],
                            "document_type": version["document_type"],
                        }
                    ),
                    *((str(actor_user_id),) if mark_approved and actor_user_id else ()),
                ),
            )
            stats["workflows"] += 1
            workflow_records.append(
                {
                    "id": workflow_id,
                    "title": group["title"],
                    "normalized_title": _normalize(group["title"]),
                    "fingerprint": fingerprint,
                    "steps": group["steps"],
                    "page": first_page,
                }
            )
            await _insert_source(
                conn,
                version,
                knowledge_type="workflow",
                knowledge_id=workflow_id,
                page=first_page,
                quote=group["title"],
            )
            stats["sources"] += 1

            step_ids_by_page: dict[str, list[tuple[UUID, dict[str, Any]]]] = defaultdict(list)
            for sequence, step in enumerate(group["steps"], start=1):
                step_id = uuid4()
                step_page = step["page"]
                await conn.execute(
                    """
                    INSERT INTO workflow_steps
                        (id, workflow_id, document_page_id, step_number, action,
                         description, ui_element, conditions, warnings, exceptions,
                         visual_callouts, confidence, status, source_page_number,
                         metadata, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, current_timestamp(), current_timestamp())
                    """,
                    (
                        str(step_id),
                        str(workflow_id),
                        str(step_page["id"]),
                        sequence,
                        step["action"][:8000],
                        step["description"][:8000],
                        step["ui_element"],
                        Jsonb(step["conditions"]),
                        Jsonb(step["warnings"]),
                        Jsonb(step["exceptions"]),
                        Jsonb(step["visual_callouts"]),
                        step["confidence"],
                        status,
                        int(step_page["physical_page_number"]),
                        Jsonb({}),
                    ),
                )
                step_ids_by_page[str(step_page["id"])].append((step_id, step))
                stats["steps"] += 1
                stats["sources"] += 1
                await _insert_source(
                    conn,
                    version,
                    knowledge_type="step",
                    knowledge_id=step_id,
                    page=step_page,
                    quote=step["action"],
                )

            for page in group["pages"]:
                analysis = page.get("analysis") or {}
                for element in analysis.get("visual_elements") or []:
                    if not isinstance(element, dict):
                        continue
                    label = str(element.get("label") or "").strip()
                    if not label:
                        continue
                    callout = element.get("callout_number")
                    matched_step = _match_step(
                        step_ids_by_page.get(str(page["id"]), []), callout
                    )
                    visual_id = uuid4()
                    await conn.execute(
                        """
                        INSERT INTO visual_references
                            (id, document_id, document_version_id, document_page_id,
                             workflow_step_id, callout_number, element_type, label,
                             action, description, bounding_box, page_number,
                             image_path, status, metadata, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, current_timestamp(), current_timestamp())
                        """,
                        (
                            str(visual_id),
                            str(version["document_id"]),
                            str(version_id),
                            str(page["id"]),
                            str(matched_step[0]) if matched_step else None,
                            callout,
                            str(element.get("element_type") or "other"),
                            label[:1000],
                            element.get("action"),
                            str(element.get("notes") or "")[:4000],
                            Jsonb(element.get("bounding_box") or {}),
                            int(page["physical_page_number"]),
                            page.get("page_image_path"),
                            status,
                            Jsonb({"callout_number": callout}),
                        ),
                    )
                    stats["visuals"] += 1

                for rule_type in (
                    "business_rule",
                    "prerequisite",
                    "validation",
                    "exception",
                    "warning",
                ):
                    key = {
                        "business_rule": "business_rules",
                        "prerequisite": "prerequisites",
                        "validation": "validations",
                        "exception": "exceptions",
                        "warning": "warnings",
                    }[rule_type]
                    for value in analysis.get(key) or []:
                        text = str(value).strip()
                        if not text:
                            continue
                        rule_id = uuid4()
                        await conn.execute(
                            """
                            INSERT INTO knowledge_rules
                                (id, document_id, document_version_id, workflow_id,
                                 rule_type, rule_text, source_page_number, status,
                                 metadata, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                                    current_timestamp(), current_timestamp())
                            """,
                            (
                                str(rule_id),
                                str(version["document_id"]),
                                str(version_id),
                                str(workflow_id),
                                rule_type,
                                text[:8000],
                                int(page["physical_page_number"]),
                                status,
                                Jsonb({}),
                            ),
                        )
                        stats["rules"] += 1
                for step in _steps_for_page(group, page):
                    for rule_type, values in (
                        ("exception", step["exceptions"]),
                        ("warning", step["warnings"]),
                    ):
                        for value in values:
                            rule_id = uuid4()
                            await conn.execute(
                                """
                                INSERT INTO knowledge_rules
                                    (id, document_id, document_version_id, workflow_id,
                                     rule_type, rule_text, source_page_number, status,
                                     metadata, created_at, updated_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                                        current_timestamp(), current_timestamp())
                                """,
                                (
                                    str(rule_id),
                                    str(version["document_id"]),
                                    str(version_id),
                                    str(workflow_id),
                                    rule_type,
                                    str(value)[:8000],
                                    int(page["physical_page_number"]),
                                    status,
                                    Jsonb({}),
                                ),
                            )
                            stats["rules"] += 1

            for page in group["pages"]:
                markdown = _page_markdown(page)
                if markdown:
                    page_markdown_id = uuid4()
                    await conn.execute(
                        """
                        INSERT INTO page_markdown
                            (id, document_page_id, document_version_id, markdown,
                             content_hash, generator, generated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, current_timestamp())
                        """,
                        (
                            str(page_markdown_id),
                            str(page["id"]),
                            str(version_id),
                            markdown,
                            sha256_text(markdown),
                            str((page.get("layout") or {}).get("format") or "vision"),
                        ),
                    )
                    stats["page_markdown"] += 1

        stats["conflicts"] += await _detect_workflow_conflicts(
            conn, version, workflow_records
        )
        stats["conflicts"] += await _detect_entity_conflicts(conn, version)

    return stats


def _workflow_groups(
    version: dict[str, Any], pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for page in pages:
        heading = _page_heading(page)
        steps = _steps_for_page(None, page)
        if not steps:
            continue
        key = _normalize(heading) or f"__document__:{version['title']}"
        analysis = page.get("analysis") or {}
        group = groups.get(key)
        if group is None:
            group = {
                "heading": heading,
                "title": heading or str(version["title"]),
                "pages": [],
                "steps": [],
                "summary_parts": [],
                "prerequisites": [],
                "warnings": [],
                "exceptions": [],
                "confidence_values": [],
                "content_type": str(page.get("content_type") or "procedure"),
            }
            groups[key] = group
        group["pages"].append(page)
        group["steps"].extend(steps)
        summary = str(analysis.get("summary") or "").strip()
        if summary:
            group["summary_parts"].append(summary)
        group["prerequisites"].extend(_strings(analysis.get("prerequisites")))
        group["warnings"].extend(_strings(analysis.get("warnings")))
        group["exceptions"].extend(_strings(analysis.get("exceptions")))
        group["confidence_values"].append(_confidence(analysis.get("confidence"), 0.6))

    result: list[dict[str, Any]] = []
    for group in groups.values():
        steps = sorted(group["steps"], key=lambda item: item["order"])
        if not steps:
            continue
        result.append(
            {
                "heading": group["heading"],
                "title": group["title"],
                "pages": group["pages"],
                "steps": steps,
                "summary": " ".join(dict.fromkeys(group["summary_parts"])),
                "prerequisites": list(dict.fromkeys(group["prerequisites"])),
                "warnings": list(dict.fromkeys(group["warnings"])),
                "exceptions": list(dict.fromkeys(group["exceptions"])),
                "confidence": round(
                    sum(group["confidence_values"]) / len(group["confidence_values"]), 4
                )
                if group["confidence_values"]
                else 0.6,
                "content_type": group["content_type"],
            }
        )
    return result


def _steps_for_page(
    group: dict[str, Any] | None, page: dict[str, Any]
) -> list[dict[str, Any]]:
    analysis = page.get("analysis") or {}
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(analysis.get("workflow_steps") or []):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if not action:
            continue
        steps.append(
            {
                "order": int(item.get("step_number") or index + 1),
                "action": action,
                "description": str(item.get("description") or ""),
                "ui_element": item.get("ui_element"),
                "conditions": _strings(item.get("conditions")),
                "warnings": _strings(item.get("warnings")),
                "exceptions": _strings(item.get("exceptions")),
                "visual_callouts": [
                    int(value)
                    for value in item.get("visual_callouts") or []
                    if str(value).isdigit()
                ],
                "confidence": _confidence(analysis.get("confidence"), 0.6),
                "page": page,
            }
        )
    if steps:
        return steps
    layout = page.get("layout") or {}
    if layout.get("format") in {"markdown", "docx"}:
        steps = _layout_steps(page) or _parse_text_steps(page)
    return steps


def _layout_steps(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Steps captured at extraction time from structured (Markdown/DOCX) text."""
    layout = page.get("layout") or {}
    raw_steps = layout.get("steps") or []
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if not action:
            continue
        steps.append(
            {
                "order": int(item.get("order") or index),
                "action": action,
                "description": str(item.get("description") or ""),
                "ui_element": None,
                "conditions": [],
                "warnings": [],
                "exceptions": [],
                "visual_callouts": [],
                "confidence": 0.75,
                "page": page,
            }
        )
    return steps


def _parse_text_steps(page: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(page.get("page_text") or "")
    steps: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _NUMBERED_STEP_RE.match(line)
        if not match:
            continue
        action = _TIMESTAMP_RE.sub("", match.group(2)).strip()
        if len(action) < 4:
            continue
        steps.append(
            {
                "order": int(match.group(1)),
                "action": action,
                "description": "",
                "ui_element": None,
                "conditions": [],
                "warnings": [],
                "exceptions": [],
                "visual_callouts": [],
                "confidence": 0.7,
                "page": page,
            }
        )
    return steps


def _page_heading(page: dict[str, Any]) -> str:
    layout = page.get("layout") or {}
    if layout.get("heading"):
        return str(layout["heading"]).strip()
    analysis = page.get("analysis") or {}
    headings = analysis.get("headings") or []
    if headings:
        return str(headings[0]).strip()
    text = str(page.get("page_text") or "").strip()
    for line in text.splitlines()[:8]:
        candidate = line.strip()
        if 3 <= len(candidate) <= 160 and not candidate.endswith((".", ";")):
            return candidate
    return ""


def _match_step(
    steps: list[tuple[UUID, dict[str, Any]]], callout: Any
) -> tuple[UUID, dict[str, Any]] | None:
    if callout is not None:
        for step_id, step in steps:
            if int(callout) in step["visual_callouts"]:
                return step_id, step
    return steps[0] if steps else None


def _page_markdown(page: dict[str, Any]) -> str:
    layout = page.get("layout") or {}
    analysis = page.get("analysis") or {}
    heading = _page_heading(page) or f"Page {page['physical_page_number']}"
    parts = [f"# {heading}"]
    summary = str(analysis.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    steps = _steps_for_page(None, page)
    if steps:
        parts.append("## Steps")
        for step in steps:
            parts.append(f"{step['order']}. {step['action']}")
    knowledge = analysis.get("domain_knowledge") or []
    if knowledge:
        parts.append("## Domain knowledge")
        for item in knowledge:
            if isinstance(item, dict):
                parts.append(
                    f"- **{item.get('name', '')}**: {item.get('description', '')}"
                )
    if layout.get("format") in {"markdown", "docx"} and not analysis:
        return str(page.get("page_text") or "").strip()
    return "\n\n".join(parts).strip()


async def _insert_source(
    conn: DatabricksConnection,
    version: dict[str, Any],
    *,
    knowledge_type: str,
    knowledge_id: UUID,
    page: dict[str, Any],
    quote: str,
) -> None:
    page_number = int(page["physical_page_number"])
    chunk_row = await (
        await conn.execute(
            """
            SELECT id FROM document_chunks
            WHERE document_version_id = %s
              AND page_start <= %s AND page_end >= %s
            ORDER BY chunk_index
            LIMIT 1
            """,
            (str(version["id"]), page_number, page_number),
        )
    ).fetchone()
    source_id = uuid4()
    await conn.execute(
        """
        INSERT INTO knowledge_sources
            (id, knowledge_type, knowledge_id, document_id, document_version_id,
             document_page_id, document_chunk_id, page_number, section_number, quote,
             metadata, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, current_timestamp())
        """,
        (
            str(source_id),
            knowledge_type,
            str(knowledge_id),
            str(version["document_id"]),
            str(version["id"]),
            str(page["id"]),
            str(chunk_row["id"]) if chunk_row else None,
            page_number,
            str((page.get("layout") or {}).get("section_number") or ""),
            quote[:4000],
            Jsonb({}),
        ),
    )


async def _detect_workflow_conflicts(
    conn: DatabricksConnection,
    version: dict[str, Any],
    workflows: list[dict[str, Any]],
) -> int:
    count = 0
    for workflow in workflows:
        if len(workflow["steps"]) < 2:
            continue
        rows = await (
            await conn.execute(
                """
                SELECT id, document_id, title,
                       get_json_object(metadata, '$.step_fingerprint') AS fingerprint
                FROM workflows
                WHERE normalized_title = %s
                  AND document_version_id <> %s
                  AND status <> 'rejected'
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (workflow["normalized_title"], str(version["id"])),
            )
        ).fetchall()
        for row in rows:
            if not row["fingerprint"] or row["fingerprint"] == workflow["fingerprint"]:
                continue

            exists = await (
                await conn.execute(
                    """
                    SELECT 1 FROM knowledge_conflicts
                    WHERE status = 'open'
                      AND (
                        (left_knowledge_id = %s AND right_knowledge_id = %s)
                        OR (left_knowledge_id = %s AND right_knowledge_id = %s)
                      )
                    """,
                    (
                        str(workflow["id"]),
                        str(row["id"]),
                        str(row["id"]),
                        str(workflow["id"]),
                    ),
                )
            ).fetchone()
            if exists:
                continue
            conflict_id = uuid4()
            await conn.execute(
                """
                INSERT INTO knowledge_conflicts
                    (id, document_id, conflict_type, left_knowledge_type,
                     left_knowledge_id, right_knowledge_type, right_knowledge_id,
                     description, status, detected_by, metadata, created_at, updated_at)
                VALUES (%s, %s, 'workflow_step_mismatch', 'workflow', %s,
                        'workflow', %s, %s, 'open', 'system', %s,
                        current_timestamp(), current_timestamp())
                """,
                (
                    str(conflict_id),
                    str(version["document_id"]),
                    str(workflow["id"]),
                    str(row["id"]),
                    f"Workflow '{workflow['title']}' has different steps in "
                    "another document version.",
                    Jsonb({}),
                ),
            )
            count += 1
            if count >= 100:
                return count
    return count


async def _detect_entity_conflicts(
    conn: DatabricksConnection, version: dict[str, Any]
) -> int:
    rows = await (
        await conn.execute(
            """
            SELECT e.id, e.normalized_name, e.entity_type, e.description,
                   other.id AS other_id, other.description AS other_description
            FROM knowledge_entities e
            JOIN knowledge_entities other
              ON other.normalized_name = e.normalized_name
             AND other.entity_type = e.entity_type
             AND other.document_version_id <> e.document_version_id
             AND other.status <> 'rejected'
            WHERE e.document_version_id = %s
              AND length(e.description) > 20
              AND length(other.description) > 20
              AND e.description <> other.description
              AND other.document_id <> e.document_id
            LIMIT 100
            """,
            (str(version["id"]),),
        )
    ).fetchall()
    count = 0
    for row in rows:
        exists = await (
            await conn.execute(
                """
                SELECT 1 FROM knowledge_conflicts
                WHERE status = 'open'
                  AND (
                    (left_knowledge_id = %s AND right_knowledge_id = %s)
                    OR (left_knowledge_id = %s AND right_knowledge_id = %s)
                  )
                """,
                (
                    str(row["id"]),
                    str(row["other_id"]),
                    str(row["other_id"]),
                    str(row["id"]),
                ),
            )
        ).fetchone()
        if exists:
            continue
        conflict_id = uuid4()
        await conn.execute(
            """
            INSERT INTO knowledge_conflicts
                (id, document_id, conflict_type, left_knowledge_type,
                 left_knowledge_id, right_knowledge_type, right_knowledge_id,
                 description, status, detected_by, metadata, created_at, updated_at)
            VALUES (%s, %s, 'entity_description_mismatch', 'entity', %s,
                    'entity', %s, %s, 'open', 'system', %s,
                    current_timestamp(), current_timestamp())
            """,
            (
                str(conflict_id),
                str(version["document_id"]),
                str(row["id"]),
                str(row["other_id"]),
                f"Entity '{row['normalized_name']}' has different descriptions "
                "across documents.",
                Jsonb({}),
            ),
        )
        count += 1
    return count


def _shared_page(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> dict[str, Any] | None:
    right_ids = {str(page["id"]) for page in right}
    for page in left:
        if str(page["id"]) in right_ids:
            return page
    return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _confidence(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "workflow"
