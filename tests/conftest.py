import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import app
from backend.app.database import Base, get_db
from backend.app.models import User, Team, Membership, WorkItem, ActivityEvent, OutboxJob
from backend.app.auth import hash_password, create_access_token

# In-memory SQLite with shared cache or static pool for thread-safe test execution
TEST_DATABASE_URL = "sqlite:///./test_runner.db"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

@pytest.fixture(autouse=True)
def clean_database():
    """Drops and recreates clean tables for each test run to ensure strict isolation."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)

@pytest.fixture
def db_session():
    """Provides a fresh database session for test assertions."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def client():
    """FastAPI TestClient yielding fresh DB sessions per endpoint request."""
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

@pytest.fixture
def seed_test_context(db_session):
    """Seeds two distinct teams and users across different roles."""
    pwd = hash_password("password123")

    # Teams
    team_a = Team(name="Alpha Platform", description="Team Alpha")
    team_b = Team(name="Beta Security", description="Team Beta")
    db_session.add_all([team_a, team_b])
    db_session.flush()

    # Users
    admin_user = User(email="admin@test.com", password_hash=pwd, full_name="Admin Alice")
    lead_a = User(email="lead.a@test.com", password_hash=pwd, full_name="Lead Alpha")
    member_a1 = User(email="member.a1@test.com", password_hash=pwd, full_name="Member Alpha 1")
    member_a2 = User(email="member.a2@test.com", password_hash=pwd, full_name="Member Alpha 2")
    viewer_a = User(email="viewer.a@test.com", password_hash=pwd, full_name="Viewer Alpha")
    outsider_b = User(email="outsider.b@test.com", password_hash=pwd, full_name="Outsider Beta")

    users = [admin_user, lead_a, member_a1, member_a2, viewer_a, outsider_b]
    db_session.add_all(users)
    db_session.flush()

    # Memberships
    db_session.add(Membership(user_id=admin_user.id, team_id=team_a.id, role="admin"))
    db_session.add(Membership(user_id=admin_user.id, team_id=team_b.id, role="admin"))

    db_session.add(Membership(user_id=lead_a.id, team_id=team_a.id, role="lead"))
    db_session.add(Membership(user_id=member_a1.id, team_id=team_a.id, role="member"))
    db_session.add(Membership(user_id=member_a2.id, team_id=team_a.id, role="member"))
    db_session.add(Membership(user_id=viewer_a.id, team_id=team_a.id, role="viewer"))

    db_session.add(Membership(user_id=outsider_b.id, team_id=team_b.id, role="member"))

    db_session.commit()

    tokens = {
        "admin": create_access_token({"sub": str(admin_user.id), "email": admin_user.email}),
        "lead_a": create_access_token({"sub": str(lead_a.id), "email": lead_a.email}),
        "member_a1": create_access_token({"sub": str(member_a1.id), "email": member_a1.email}),
        "member_a2": create_access_token({"sub": str(member_a2.id), "email": member_a2.email}),
        "viewer_a": create_access_token({"sub": str(viewer_a.id), "email": viewer_a.email}),
        "outsider_b": create_access_token({"sub": str(outsider_b.id), "email": outsider_b.email})
    }

    return {
        "teams": {"a": team_a, "b": team_b},
        "users": {
            "admin": admin_user,
            "lead_a": lead_a,
            "member_a1": member_a1,
            "member_a2": member_a2,
            "viewer_a": viewer_a,
            "outsider_b": outsider_b
        },
        "tokens": tokens
    }
