from datetime import datetime, timezone
import json
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey,
    UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from backend.app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    memberships = relationship("Membership", back_populates="user", cascade="all, delete-orphan")
    assigned_items = relationship("WorkItem", foreign_keys="WorkItem.assignee_id", back_populates="assignee")
    created_items = relationship("WorkItem", foreign_keys="WorkItem.created_by_id", back_populates="creator")
    comments = relationship("Comment", back_populates="author")

class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    memberships = relationship("Membership", back_populates="team", cascade="all, delete-orphan")
    work_items = relationship("WorkItem", back_populates="team", cascade="all, delete-orphan")

class Membership(Base):
    __tablename__ = "memberships"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    # Role values: 'viewer', 'member', 'lead', 'admin'
    role = Column(String(50), nullable=False, default="member")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    user = relationship("User", back_populates="memberships")
    team = relationship("Team", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("user_id", "team_id", name="uq_user_team_membership"),
    )

class WorkItem(Base):
    __tablename__ = "work_items"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    # Types: incident, customer_issue, payment, compliance, task
    item_type = Column(String(50), nullable=False, default="task", index=True)
    # Priority: urgent, high, medium, low
    priority = Column(String(50), nullable=False, default="medium", index=True)
    # Status: open, in_progress, awaiting_approval, resolved, closed
    status = Column(String(50), nullable=False, default="open", index=True)

    team_id = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    assignee_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)

    # Concurrency control: monotonic version number incremented on each update
    version = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    team = relationship("Team", back_populates="work_items")
    assignee = relationship("User", foreign_keys=[assignee_id], back_populates="assigned_items")
    creator = relationship("User", foreign_keys=[created_by_id], back_populates="created_items")
    activity_events = relationship("ActivityEvent", back_populates="work_item", cascade="all, delete-orphan", order_by="ActivityEvent.created_at.desc()")
    comments = relationship("Comment", back_populates="work_item", cascade="all, delete-orphan", order_by="Comment.created_at.asc()")

    __table_args__ = (
        Index("ix_work_items_team_status_prio", "team_id", "status", "priority"),
        Index("ix_work_items_assignee_status", "assignee_id", "status"),
    )

class ActivityEvent(Base):
    """Append-only audit trail. Every mutation must write an activity event in the SAME transaction."""
    __tablename__ = "activity_events"

    id = Column(Integer, primary_key=True, index=True)
    work_item_id = Column(Integer, ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_type = Column(String(50), nullable=False)  # created, claimed, status_changed, priority_changed, assignee_changed, updated, commented
    from_state = Column(String(100), nullable=True)
    to_state = Column(String(100), nullable=True)
    details = Column(Text, nullable=True)  # JSON or descriptive text
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    work_item = relationship("WorkItem", back_populates="activity_events")
    actor = relationship("User")

class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    work_item_id = Column(Integer, ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    work_item = relationship("WorkItem", back_populates="comments")
    author = relationship("User", back_populates="comments")

class IdempotencyKey(Base):
    """Stores idempotency keys to ensure retries or double-clicks return identical cached responses."""
    __tablename__ = "idempotency_keys"

    key = Column(String(128), primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    endpoint = Column(String(255), nullable=False)
    request_hash = Column(String(64), nullable=False)
    response_status = Column(Integer, nullable=False)
    response_body = Column(Text, nullable=False)  # JSON serialized response
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

class OutboxJob(Base):
    """Transactional Outbox Pattern. Jobs created atomically inside the mutation transaction."""
    __tablename__ = "outbox_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String(100), nullable=False, index=True)  # notify_new_assignee, escalate_urgent, audit_sync
    payload = Column(Text, nullable=False)  # JSON string
    status = Column(String(50), nullable=False, default="pending", index=True)  # pending, processing, completed, failed
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
