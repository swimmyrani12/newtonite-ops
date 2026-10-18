from typing import Optional, List, Any
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict

# --- Auth & User ---
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserWithRolesResponse"

class UserBase(BaseModel):
    email: EmailStr
    full_name: str

class UserResponse(UserBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class TeamRoleInfo(BaseModel):
    team_id: int
    team_name: str
    role: str

class UserWithRolesResponse(UserResponse):
    roles: List[TeamRoleInfo] = []

# --- Teams & Memberships ---
class TeamBase(BaseModel):
    name: str
    description: Optional[str] = None

class TeamResponse(TeamBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- Comments ---
class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)

class CommentResponse(BaseModel):
    id: int
    work_item_id: int
    author_id: Optional[int]
    author_name: Optional[str] = None
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- Activity Events ---
class ActivityEventResponse(BaseModel):
    id: int
    work_item_id: int
    actor_id: Optional[int]
    actor_name: Optional[str] = None
    event_type: str
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    details: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- Work Items ---
class WorkItemCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    item_type: str = Field(default="task", description="incident, customer_issue, payment, compliance, task")
    priority: str = Field(default="medium", description="urgent, high, medium, low")
    team_id: int
    assignee_id: Optional[int] = None
    due_date: Optional[datetime] = None

class WorkItemUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = None
    priority: Optional[str] = None
    assignee_id: Optional[int] = None
    due_date: Optional[datetime] = None
    version: int = Field(..., description="Current version on client for optimistic locking check")

class WorkItemTransition(BaseModel):
    to_status: str = Field(..., description="Target status: open, in_progress, awaiting_approval, resolved, closed")
    version: int = Field(..., description="Current version on client for optimistic locking check")
    comment: Optional[str] = Field(None, max_length=1000)

class WorkItemClaim(BaseModel):
    version: Optional[int] = None

class WorkItemResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    item_type: str
    priority: str
    status: str
    team_id: int
    team_name: Optional[str] = None
    assignee_id: Optional[int] = None
    assignee_name: Optional[str] = None
    created_by_id: Optional[int] = None
    creator_name: Optional[str] = None
    due_date: Optional[datetime] = None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class WorkItemDetailResponse(WorkItemResponse):
    activity_events: List[ActivityEventResponse] = []
    comments: List[CommentResponse] = []

class PaginatedWorkItemsResponse(BaseModel):
    items: List[WorkItemResponse]
    next_cursor: Optional[int] = None
    prev_cursor: Optional[int] = None
    has_more: bool
    total_count: int

# --- Dashboard Stats ---
class DashboardStatsResponse(BaseModel):
    assigned_to_me: int
    unassigned_urgent: int
    awaiting_approval: int
    total_team_items: int

# Update forward refs
TokenResponse.model_rebuild()
UserWithRolesResponse.model_rebuild()
