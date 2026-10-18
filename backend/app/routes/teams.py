from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from backend.app.database import get_db
from backend.app.models import Team, Membership, User
from backend.app.auth import get_current_user
from backend.app.permissions import check_team_permission

router = APIRouter(prefix="/api/teams", tags=["Teams"])

@router.get("")
def list_teams(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    teams = db.query(Team).order_by(Team.name.asc()).all()
    user_team_map = {m.team_id: m.role for m in current_user.memberships}

    result = []
    for t in teams:
        result.append({
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "created_at": t.created_at,
            "user_role": user_team_map.get(t.id, None),
            "is_member": t.id in user_team_map
        })
    return result

@router.get("/{team_id}/members")
def get_team_members(
    team_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    check_team_permission(current_user, team_id, required_role="viewer")
    memberships = (
        db.query(Membership)
        .options(joinedload(Membership.user))
        .filter(Membership.team_id == team_id)
        .all()
    )
    return [
        {
            "id": m.user.id,
            "full_name": m.user.full_name,
            "email": m.user.email,
            "role": m.role
        }
        for m in memberships
    ]
