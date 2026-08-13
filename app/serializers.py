from app.models import COMMENT_KIND_LABELS, INCIDENT_STATUS_LABELS, Incident


def incident_to_dict(incident: Incident) -> dict:
    return {
        "id": incident.id,
        "department": incident.department.name,
        "department_id": incident.department_id,
        "site": incident.site.name,
        "site_id": incident.site_id,
        "reporter_name": incident.reporter_name,
        "reporter_rank": incident.reporter_rank,
        "reporter_contact": incident.reporter_contact,
        "reporter_summary": incident.reporter_summary,
        "occurred_at": incident.occurred_at.isoformat() if incident.occurred_at else None,
        "location": incident.location,
        "background": incident.background,
        "situation": incident.situation,
        "action_taken": incident.action_taken,
        "damage": incident.damage,
        "statement": incident.statement,
        "status": incident.status,
        "status_label": INCIDENT_STATUS_LABELS[incident.status],
        "citations": incident.citations or [],
        "reviewer_note": incident.reviewer_note,
        "created_by": incident.created_by.display_name if incident.created_by else None,
        "created_by_user_id": incident.created_by_user_id,
        "assigned_manager": incident.assigned_manager.display_name if incident.assigned_manager else None,
        "assigned_manager_id": incident.assigned_manager_id,
        "created_at": incident.created_at.isoformat(),
        "events": [
            {
                "status": e.status,
                "status_label": INCIDENT_STATUS_LABELS[e.status],
                "note": e.note,
                "actor": e.actor.display_name if e.actor else None,
                "created_at": e.created_at.isoformat(),
            }
            for e in incident.events
        ],
        "comments": [
            {
                "id": c.id,
                "kind": c.kind,
                "kind_label": COMMENT_KIND_LABELS.get(c.kind, c.kind),
                "body": c.body,
                "author": c.author.display_name if c.author else None,
                "author_role": c.author.role if c.author else None,
                "created_at": c.created_at.isoformat(),
            }
            for c in incident.comments
        ],
        # 파일 내용(bytes)은 목록 응답에 절대 포함하지 않는다 — 실제 데이터는
        # /api/incidents/{id}/attachments/{attachment_id}에서 별도로 내려준다.
        "attachments": [
            {
                "id": a.id,
                "filename": a.filename,
                "content_type": a.content_type,
                "size_bytes": a.size_bytes,
                "uploaded_by": a.uploaded_by.display_name if a.uploaded_by else None,
                "created_at": a.created_at.isoformat(),
            }
            for a in incident.attachments
        ],
    }
