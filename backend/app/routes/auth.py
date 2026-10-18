from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from backend.app.database import get_db
from backend.app.models import User, Membership
from backend.app.schemas import LoginRequest, TokenResponse, UserWithRolesResponse, TeamRoleInfo
from backend.app.auth import verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

def format_user_with_roles(user: User) -> UserWithRolesResponse:
    roles = [
        TeamRoleInfo(
            team_id=m.team_id,
            team_name=m.team.name if m.team else f"Team #{m.team_id}",
            role=m.role
        )
        for m in user.memberships
    ]
    return UserWithRolesResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        created_at=user.created_at,
        roles=roles
    )

@router.post("/login", response_model=TokenResponse)
def login(creds: LoginRequest, db: Session = Depends(get_db)):
    user = (
        db.query(User)
        .options(joinedload(User.memberships).joinedload(Membership.team))
        .filter(User.email == creds.email.lower().strip())
        .first()
    )
    if not user or not verify_password(creds.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": str(user.id), "email": user.email})
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user=format_user_with_roles(user)
    )

@router.get("/me", response_model=UserWithRolesResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return format_user_with_roles(current_user)
