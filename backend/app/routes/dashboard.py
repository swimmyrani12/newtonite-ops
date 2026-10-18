from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.models import WorkItem, User, Membership
from backend.app.schemas import DashboardStatsResponse
from backend.app.auth import get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

@router.get("/stats", response_model=DashboardStatsResponse)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    user_team_ids = [m.team_id for m in current_user.memberships]
    lead_team_ids = [m.team_id for m in current_user.memberships if m.role.lower() in ("lead", "admin")]

    if not user_team_ids:
        return DashboardStatsResponse(
            assigned_to_me=0,
            unassigned_urgent=0,
            awaiting_approval=0,
            total_team_items=0
        )

    # 1. Assigned to me (active)
    assigned_to_me = (
        db.query(WorkItem)
        .filter(
            WorkItem.assignee_id == current_user.id,
            WorkItem.status.in_(["open", "in_progress", "awaiting_approval"])
        )
        .count()
    )

    # 2. Unassigned urgent across user's teams
    unassigned_urgent = (
        db.query(WorkItem)
        .filter(
            WorkItem.team_id.in_(user_team_ids),
            WorkItem.assignee_id.is_(None),
            WorkItem.priority == "urgent",
            WorkItem.status.in_(["open", "in_progress"])
        )
        .count()
    )

    # 3. Awaiting approval (in teams where user is lead/admin)
    if lead_team_ids:
        awaiting_approval = (
            db.query(WorkItem)
            .filter(
                WorkItem.team_id.in_(lead_team_ids),
                WorkItem.status == "awaiting_approval"
            )
            .count()
        )
    else:
        awaiting_approval = 0

    # 4. Total active items in user's teams
    total_team_items = (
        db.query(WorkItem)
        .filter(
            WorkItem.team_id.in_(user_team_ids),
            WorkItem.status != "closed"
        )
        .count()
    )

    return DashboardStatsResponse(
        assigned_to_me=assigned_to_me,
        unassigned_urgent=unassigned_urgent,
        awaiting_approval=awaiting_approval,
        total_team_items=total_team_items
    )
