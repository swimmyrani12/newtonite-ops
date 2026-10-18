import json
import hashlib
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Header, status, Response
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_, desc

from backend.app.database import get_db
from backend.app.models import (
    WorkItem, ActivityEvent, Comment, IdempotencyKey, OutboxJob, User, Team, utcnow
)
from backend.app.schemas import (
    WorkItemCreate, WorkItemUpdate, WorkItemTransition, WorkItemClaim,
    WorkItemResponse, WorkItemDetailResponse, PaginatedWorkItemsResponse,
    CommentCreate, CommentResponse, ActivityEventResponse
)
from backend.app.auth import get_current_user
from backend.app.permissions import check_team_permission, validate_state_transition

router = APIRouter(prefix="/api/items", tags=["Work Items"])

def serialize_item(item: WorkItem) -> WorkItemResponse:
    return WorkItemResponse(
        id=item.id,
        title=item.title,
        description=item.description,
        item_type=item.item_type,
        priority=item.priority,
        status=item.status,
        team_id=item.team_id,
        team_name=item.team.name if item.team else None,
        assignee_id=item.assignee_id,
        assignee_name=item.assignee.full_name if item.assignee else None,
        created_by_id=item.created_by_id,
        creator_name=item.creator.full_name if item.creator else None,
        due_date=item.due_date,
        version=item.version,
        created_at=item.created_at,
        updated_at=item.updated_at
    )

def serialize_item_detail(item: WorkItem) -> WorkItemDetailResponse:
    base = serialize_item(item)
    events = [
        ActivityEventResponse(
            id=e.id,
            work_item_id=e.work_item_id,
            actor_id=e.actor_id,
            actor_name=e.actor.full_name if e.actor else "System",
            event_type=e.event_type,
            from_state=e.from_state,
            to_state=e.to_state,
            details=e.details,
            created_at=e.created_at
        )
        for e in item.activity_events
    ]
    comments = [
        CommentResponse(
            id=c.id,
            work_item_id=c.work_item_id,
            author_id=c.author_id,
            author_name=c.author.full_name if c.author else "Unknown",
            content=c.content,
            created_at=c.created_at
        )
        for c in item.comments
    ]
    return WorkItemDetailResponse(
        **base.model_dump(),
        activity_events=events,
        comments=comments
    )

@router.get("", response_model=PaginatedWorkItemsResponse)
def list_items(
    team_id: Optional[int] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assignee: Optional[str] = None,
    needs_attention: Optional[str] = None,
    q: Optional[str] = None,
    cursor: Optional[int] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List work items with server-side filtering, text search, and cursor-based pagination.
    Resource-level authorization: Enforces that queries are restricted to teams the user belongs to.
    """
    user_team_ids = [m.team_id for m in current_user.memberships]
    if not user_team_ids:
        return PaginatedWorkItemsResponse(items=[], next_cursor=None, prev_cursor=None, has_more=False, total_count=0)

    # If team_id specified, ensure user has access to it
    if team_id:
        check_team_permission(current_user, team_id, required_role="viewer")
        target_team_ids = [team_id]
    else:
        target_team_ids = user_team_ids

    query = (
        db.query(WorkItem)
        .options(
            joinedload(WorkItem.team),
            joinedload(WorkItem.assignee),
            joinedload(WorkItem.creator)
        )
        .filter(WorkItem.team_id.in_(target_team_ids))
    )

    # 1. Server-side filtering
    if status:
        query = query.filter(WorkItem.status == status.lower().strip())
    if priority:
        query = query.filter(WorkItem.priority == priority.lower().strip())

    if assignee:
        if assignee == "me":
            query = query.filter(WorkItem.assignee_id == current_user.id)
        elif assignee == "unassigned":
            query = query.filter(WorkItem.assignee_id.is_(None))
        else:
            try:
                aid = int(assignee)
                query = query.filter(WorkItem.assignee_id == aid)
            except ValueError:
                pass

    # 2. Needs Attention presets
    if needs_attention == "assigned_to_me":
        query = query.filter(
            WorkItem.assignee_id == current_user.id,
            WorkItem.status.in_(["open", "in_progress", "awaiting_approval"])
        )
    elif needs_attention == "unassigned_urgent":
        query = query.filter(
            WorkItem.assignee_id.is_(None),
            WorkItem.priority == "urgent",
            WorkItem.status.in_(["open", "in_progress"])
        )
    elif needs_attention == "awaiting_approval":
        lead_team_ids = [m.team_id for m in current_user.memberships if m.role.lower() in ("lead", "admin")]
        query = query.filter(
            WorkItem.team_id.in_(lead_team_ids),
            WorkItem.status == "awaiting_approval"
        )

    # 3. Server-side text search (title & description)
    if q and q.strip():
        search_pattern = f"%{q.strip()}%"
        query = query.filter(
            or_(
                WorkItem.title.ilike(search_pattern),
                WorkItem.description.ilike(search_pattern)
            )
        )

    total_count = query.count()

    # 4. Cursor pagination (ordered by id DESC for monotonic stability)
    if cursor is not None:
        query = query.filter(WorkItem.id < cursor)

    limit = max(1, min(limit, 100))
    items = query.order_by(desc(WorkItem.id)).limit(limit + 1).all()

    has_more = len(items) > limit
    paginated_items = items[:limit]
    next_cursor = paginated_items[-1].id if has_more and paginated_items else None

    return PaginatedWorkItemsResponse(
        items=[serialize_item(i) for i in paginated_items],
        next_cursor=next_cursor,
        prev_cursor=None,
        has_more=has_more,
        total_count=total_count
    )

@router.post("", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: WorkItemCreate,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Creates a new work item.
    Supports Idempotency-Key header to prevent duplicate items from retries or double-clicks.
    Records creation in activity log and transactional outbox inside the same transaction.
    """
    check_team_permission(current_user, payload.team_id, required_role="member")

    endpoint = "/api/items"
    request_hash = hashlib.sha256(
        f"{payload.team_id}:{payload.title}:{payload.item_type}:{payload.priority}".encode()
    ).hexdigest()

    # 1. Idempotency Check
    if idempotency_key:
        cached = (
            db.query(IdempotencyKey)
            .filter(
                IdempotencyKey.key == idempotency_key,
                IdempotencyKey.user_id == current_user.id,
                IdempotencyKey.endpoint == endpoint
            )
            .first()
        )
        if cached:
            response.status_code = cached.response_status
            return json.loads(cached.response_body)

    # 2. Open Atomic Transaction
    try:
        new_item = WorkItem(
            title=payload.title.strip(),
            description=payload.description.strip() if payload.description else None,
            item_type=payload.item_type.lower(),
            priority=payload.priority.lower(),
            status="open",
            team_id=payload.team_id,
            assignee_id=payload.assignee_id,
            created_by_id=current_user.id,
            due_date=payload.due_date,
            version=1
        )
        db.add(new_item)
        db.flush()  # Flush to generate new_item.id

        # Append-only Activity Event inside SAME transaction
        activity = ActivityEvent(
            work_item_id=new_item.id,
            actor_id=current_user.id,
            event_type="created",
            from_state=None,
            to_state="open",
            details=f"Work item '{new_item.title}' created"
        )
        db.add(activity)

        # Transactional Outbox Job inside SAME transaction
        if new_item.assignee_id:
            outbox_job = OutboxJob(
                job_type="notify_new_assignee",
                payload=json.dumps({
                    "item_id": new_item.id,
                    "title": new_item.title,
                    "assignee_id": new_item.assignee_id,
                    "assigned_by": current_user.full_name
                }),
                status="pending"
            )
            db.add(outbox_job)

        db.commit()
        db.refresh(new_item)

        serialized = serialize_item(new_item).model_dump(mode="json")

        # Cache response in idempotency_keys
        if idempotency_key:
            idemp = IdempotencyKey(
                key=idempotency_key,
                user_id=current_user.id,
                endpoint=endpoint,
                request_hash=request_hash,
                response_status=201,
                response_body=json.dumps(serialized)
            )
            db.add(idemp)
            db.commit()

        return serialized
    except Exception:
        db.rollback()
        raise

@router.get("/{item_id}", response_model=WorkItemDetailResponse)
def get_item_detail(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get full work item detail including activity audit timeline and comments.
    Enforces resource-level authorization: User must belong to the item's team.
    """
    item = (
        db.query(WorkItem)
        .options(
            joinedload(WorkItem.team),
            joinedload(WorkItem.assignee),
            joinedload(WorkItem.creator),
            joinedload(WorkItem.activity_events).joinedload(ActivityEvent.actor),
            joinedload(WorkItem.comments).joinedload(Comment.author)
        )
        .filter(WorkItem.id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Work item {item_id} not found")

    # Resource-level authorization
    check_team_permission(current_user, item.team_id, required_role="viewer")

    return serialize_item_detail(item)

@router.post("/{item_id}/claim", response_model=WorkItemResponse)
def claim_item(
    item_id: int,
    payload: Optional[WorkItemClaim] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Atomic Conditional Claim:
    Ensures that when two users attempt to claim the same unassigned item simultaneously,
    exactly one succeeds, and the other receives a 409 Conflict.
    SQL: UPDATE work_items SET assignee_id = ?, version = version + 1 WHERE id = ? AND assignee_id IS NULL;
    """
    # First check existence and permission
    item = db.query(WorkItem).filter(WorkItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Work item {item_id} not found")

    check_team_permission(current_user, item.team_id, required_role="member")

    # Atomic conditional update
    update_clause = (
        db.query(WorkItem)
        .filter(WorkItem.id == item_id, WorkItem.assignee_id.is_(None))
    )
    if payload and payload.version is not None:
        update_clause = update_clause.filter(WorkItem.version == payload.version)

    rows_updated = update_clause.update(
        {
            "assignee_id": current_user.id,
            "version": item.version + 1,
            "updated_at": utcnow()
        },
        synchronize_session=False
    )

    if rows_updated == 0:
        # Item was either already claimed or version mismatched
        fresh_item = db.query(WorkItem).filter(WorkItem.id == item_id).first()
        if fresh_item and fresh_item.assignee_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Conflict: Item has already been claimed by user ID {fresh_item.assignee_id}."
            )
        else:
            ver = fresh_item.version if fresh_item else item.version
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Conflict: Version mismatch. Current version is {ver}."
            )

    # In the SAME transaction: record ActivityEvent and Transactional Outbox Job
    try:
        event = ActivityEvent(
            work_item_id=item_id,
            actor_id=current_user.id,
            event_type="claimed",
            from_state="unassigned",
            to_state=current_user.full_name,
            details=f"Item claimed by {current_user.full_name}"
        )
        db.add(event)

        outbox_job = OutboxJob(
            job_type="notify_new_assignee",
            payload=json.dumps({
                "item_id": item_id,
                "title": item.title,
                "assignee_id": current_user.id,
                "claimed_by": current_user.full_name
            }),
            status="pending"
        )
        db.add(outbox_job)

        db.commit()
    except Exception:
        db.rollback()
        raise

    fresh_item = (
        db.query(WorkItem)
        .options(joinedload(WorkItem.team), joinedload(WorkItem.assignee), joinedload(WorkItem.creator))
        .filter(WorkItem.id == item_id)
        .first()
    )
    return serialize_item(fresh_item)

@router.patch("/{item_id}", response_model=WorkItemResponse)
def update_item(
    item_id: int,
    payload: WorkItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Optimistic Concurrency Control:
    The client sends the current version. The server updates only WHERE version = :client_version.
    If 0 rows are updated, returns a 409 Conflict with the current version and data.
    """
    item = db.query(WorkItem).filter(WorkItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Work item {item_id} not found")

    check_team_permission(current_user, item.team_id, required_role="member")

    # Build update dict
    update_data = {"version": WorkItem.version + 1, "updated_at": utcnow()}
    changes = []

    if payload.title is not None and payload.title.strip() != item.title:
        update_data["title"] = payload.title.strip()
        changes.append(f"Title changed to '{payload.title.strip()}'")

    if payload.description is not None and payload.description != item.description:
        update_data["description"] = payload.description.strip()
        changes.append("Description updated")

    if payload.priority is not None and payload.priority.lower() != item.priority:
        update_data["priority"] = payload.priority.lower()
        changes.append(f"Priority changed from {item.priority} to {payload.priority.lower()}")

    if payload.assignee_id != item.assignee_id:
        update_data["assignee_id"] = payload.assignee_id
        changes.append(f"Assignee changed to {payload.assignee_id}")

    if payload.due_date is not None:
        update_data["due_date"] = payload.due_date
        changes.append("Due date updated")

    # Conditional update enforcing version check
    rows_updated = (
        db.query(WorkItem)
        .filter(WorkItem.id == item_id, WorkItem.version == payload.version)
        .update(update_data, synchronize_session=False)
    )

    if rows_updated == 0:
        fresh = db.query(WorkItem).filter(WorkItem.id == item_id).first()
        latest_ver = fresh.version if fresh else item.version
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conflict: Item was modified by another user. Your version was {payload.version}, but current version is {latest_ver}. Please reload the item."
        )

    # In the SAME transaction: write activity event
    try:
        event = ActivityEvent(
            work_item_id=item_id,
            actor_id=current_user.id,
            event_type="updated",
            details="; ".join(changes) if changes else "Updated item"
        )
        db.add(event)

        if "assignee_id" in update_data and update_data["assignee_id"]:
            outbox = OutboxJob(
                job_type="notify_new_assignee",
                payload=json.dumps({
                    "item_id": item_id,
                    "title": item.title,
                    "assignee_id": update_data["assignee_id"],
                    "updated_by": current_user.full_name
                }),
                status="pending"
            )
            db.add(outbox)

        db.commit()
    except Exception:
        db.rollback()
        raise

    fresh = (
        db.query(WorkItem)
        .options(joinedload(WorkItem.team), joinedload(WorkItem.assignee), joinedload(WorkItem.creator))
        .filter(WorkItem.id == item_id)
        .first()
    )
    return serialize_item(fresh)

@router.post("/{item_id}/transition", response_model=WorkItemResponse)
def transition_item(
    item_id: int,
    payload: WorkItemTransition,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Workflow State Machine Transition:
    - Server-enforced state machine (Open -> In Progress -> Awaiting Approval -> Resolved -> Closed).
    - Role enforcement (e.g. Awaiting Approval -> Resolved requires Lead or Admin).
    - Optimistic version check: rejects if client version is stale.
    - Atomically writes activity audit log and outbox job in the same transaction.
    """
    item = db.query(WorkItem).filter(WorkItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Work item {item_id} not found")

    membership = check_team_permission(current_user, item.team_id, required_role="member")
    validate_state_transition(item, payload.to_status, membership.role)

    old_status = item.status
    target_status = payload.to_status.lower()

    # Conditional update enforcing version check
    rows_updated = (
        db.query(WorkItem)
        .filter(WorkItem.id == item_id, WorkItem.version == payload.version)
        .update(
            {
                "status": target_status,
                "version": item.version + 1,
                "updated_at": utcnow()
            },
            synchronize_session=False
        )
    )

    if rows_updated == 0:
        fresh = db.query(WorkItem).filter(WorkItem.id == item_id).first()
        latest_ver = fresh.version if fresh else item.version
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conflict: Item was modified concurrently. Your version was {payload.version}, current version is {latest_ver}. Please refresh."
        )

    # In the SAME transaction: write activity event, optional comment, and outbox job
    try:
        detail_msg = f"Transitioned from {old_status} to {target_status}"
        if payload.comment:
            detail_msg += f". Note: {payload.comment}"

        event = ActivityEvent(
            work_item_id=item_id,
            actor_id=current_user.id,
            event_type="status_changed",
            from_state=old_status,
            to_state=target_status,
            details=detail_msg
        )
        db.add(event)

        if payload.comment:
            comm = Comment(
                work_item_id=item_id,
                author_id=current_user.id,
                content=payload.comment
            )
            db.add(comm)

        outbox = OutboxJob(
            job_type="status_transition",
            payload=json.dumps({
                "item_id": item_id,
                "from_state": old_status,
                "to_state": target_status,
                "actor": current_user.full_name,
                "comment": payload.comment
            }),
            status="pending"
        )
        db.add(outbox)

        db.commit()
    except Exception:
        db.rollback()
        raise

    fresh = (
        db.query(WorkItem)
        .options(joinedload(WorkItem.team), joinedload(WorkItem.assignee), joinedload(WorkItem.creator))
        .filter(WorkItem.id == item_id)
        .first()
    )
    return serialize_item(fresh)

@router.get("/{item_id}/activity", response_model=List[ActivityEventResponse])
def get_item_activity(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Returns append-only history of every change for a work item."""
    item = db.query(WorkItem).filter(WorkItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Work item {item_id} not found")

    check_team_permission(current_user, item.team_id, required_role="viewer")

    events = (
        db.query(ActivityEvent)
        .options(joinedload(ActivityEvent.actor))
        .filter(ActivityEvent.work_item_id == item_id)
        .order_by(desc(ActivityEvent.created_at))
        .all()
    )
    return [
        ActivityEventResponse(
            id=e.id,
            work_item_id=e.work_item_id,
            actor_id=e.actor_id,
            actor_name=e.actor.full_name if e.actor else "System",
            event_type=e.event_type,
            from_state=e.from_state,
            to_state=e.to_state,
            details=e.details,
            created_at=e.created_at
        )
        for e in events
    ]

@router.post("/{item_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
def add_comment(
    item_id: int,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Adds a comment and logs an activity event in the same transaction."""
    item = db.query(WorkItem).filter(WorkItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Work item {item_id} not found")

    check_team_permission(current_user, item.team_id, required_role="member")

    try:
        comment = Comment(
            work_item_id=item_id,
            author_id=current_user.id,
            content=payload.content.strip()
        )
        db.add(comment)
        db.flush()

        event = ActivityEvent(
            work_item_id=item_id,
            actor_id=current_user.id,
            event_type="commented",
            details=f"Comment added by {current_user.full_name}"
        )
        db.add(event)

        db.commit()
        db.refresh(comment)

        return CommentResponse(
            id=comment.id,
            work_item_id=comment.work_item_id,
            author_id=comment.author_id,
            author_name=current_user.full_name,
            content=comment.content,
            created_at=comment.created_at
        )
    except Exception:
        db.rollback()
        raise
