from typing import Dict, List, Optional
from fastapi import HTTPException, status
from backend.app.models import User, Membership, WorkItem

# Valid roles in order of increasing authority
ROLE_HIERARCHY = {
    "viewer": 10,
    "member": 20,
    "lead": 30,
    "admin": 40
}

# Finite State Machine for Work Item Life Cycle
ALLOWED_TRANSITIONS: Dict[str, List[str]] = {
    "open": ["in_progress"],
    "in_progress": ["awaiting_approval", "open"],
    "awaiting_approval": ["resolved", "in_progress"],
    "resolved": ["closed", "in_progress"],
    "closed": ["open"]
}

# Transitions that require elevated authority (Lead or Admin)
LEAD_ONLY_TRANSITIONS = {
    ("awaiting_approval", "resolved"),
    ("resolved", "closed")
}

def get_user_team_membership(user: User, team_id: int) -> Optional[Membership]:
    """Finds the user's membership for a specific team."""
    for m in user.memberships:
        if m.team_id == team_id:
            return m
    return None

def check_team_permission(user: User, team_id: int, required_role: str = "viewer") -> Membership:
    """
    Enforces resource-level authorization.
    Verifies the user belongs to team_id and has at least required_role level.
    Raises 403 Forbidden if unauthorized.
    """
    membership = get_user_team_membership(user, team_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: User '{user.email}' is not a member of team ID {team_id}."
        )

    user_level = ROLE_HIERARCHY.get(membership.role.lower(), 0)
    required_level = ROLE_HIERARCHY.get(required_role.lower(), 0)

    if user_level < required_level:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: Action requires '{required_role}' role or higher. Your role in team ID {team_id} is '{membership.role}'."
        )

    return membership

def validate_state_transition(item: WorkItem, to_status: str, actor_role: str) -> None:
    """
    Validates state machine transition rules and role enforcement.
    Raises 400 Bad Request on invalid state transition.
    Raises 403 Forbidden if transition requires Lead/Admin and actor lacks permissions.
    """
    current_status = item.status.lower()
    target_status = to_status.lower()

    if current_status == target_status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Work item is already in '{current_status}' status."
        )

    allowed = ALLOWED_TRANSITIONS.get(current_status, [])
    if target_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Illegal transition: Cannot transition from '{current_status}' to '{target_status}'. Allowed transitions: {allowed}."
        )

    # Check if this specific transition requires Lead or Admin
    if (current_status, target_status) in LEAD_ONLY_TRANSITIONS:
        user_level = ROLE_HIERARCHY.get(actor_role.lower(), 0)
        lead_level = ROLE_HIERARCHY.get("lead", 30)
        if user_level < lead_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Approval/Resolution requires 'lead' or 'admin' role. Your role is '{actor_role}'."
            )
