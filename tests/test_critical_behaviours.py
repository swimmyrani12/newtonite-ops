import concurrent.futures
import json
import pytest
from fastapi.testclient import TestClient
from backend.app.models import WorkItem, ActivityEvent, OutboxJob
from backend.app.worker import process_outbox_batch, process_single_job, PROCESSED_DISPATCHES

# 1. Critical Behaviour: Concurrency Control - Concurrent Claim
def test_concurrent_claim_only_one_wins(client, seed_test_context, db_session):
    """
    Two users claim the same item simultaneously.
    Server uses conditional update:
    UPDATE work_items SET assignee_id = ? WHERE id = ? AND assignee_id IS NULL;
    Assert: Exactly one claim succeeds (200), the other is rejected (409 Conflict).
    """
    ctx = seed_test_context
    team_a = ctx["teams"]["a"]
    user_creator = ctx["users"]["admin"]
    token_m1 = ctx["tokens"]["member_a1"]
    token_m2 = ctx["tokens"]["member_a2"]

    # Create an unassigned item
    item = WorkItem(
        title="Unassigned Incident for Concurrent Claim",
        description="High urgency server incident",
        team_id=team_a.id,
        status="open",
        priority="urgent",
        assignee_id=None,
        created_by_id=user_creator.id,
        version=1
    )
    db_session.add(item)
    db_session.commit()
    item_id = item.id

    def try_claim(token):
        return client.post(
            f"/api/items/{item_id}/claim",
            headers={"Authorization": f"Bearer {token}"},
            json={"version": 1}
        )

    # Fire two claims in parallel threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(try_claim, token_m1)
        f2 = executor.submit(try_claim, token_m2)
        res1 = f1.result()
        res2 = f2.result()

    statuses = [res1.status_code, res2.status_code]
    assert 200 in statuses, f"Expected one successful claim (200), got {statuses}"
    assert 409 in statuses, f"Expected one 409 conflict, got {statuses}"

    # Verify database state
    db_session.expire_all()
    updated_item = db_session.query(WorkItem).filter(WorkItem.id == item_id).first()
    assert updated_item.assignee_id is not None
    assert updated_item.version == 2

    # Verify activity audit event was written
    events = db_session.query(ActivityEvent).filter(ActivityEvent.work_item_id == item_id).all()
    assert any(e.event_type == "claimed" for e in events)

# 2. Critical Behaviour: Concurrency Control - Optimistic Locking / Stale Update
def test_stale_update_rejected(client, seed_test_context, db_session):
    """
    Optimistic locking: Client sends current version.
    If another user updated the item in the meantime, the stale update is rejected with 409 Conflict.
    Assert: Data remains unchanged from stale write.
    """
    ctx = seed_test_context
    team_a = ctx["teams"]["a"]
    token_m1 = ctx["tokens"]["member_a1"]
    token_m2 = ctx["tokens"]["member_a2"]

    # Create item at version 1
    item = WorkItem(
        title="Original Title v1",
        description="Initial description",
        team_id=team_a.id,
        status="open",
        priority="medium",
        version=1
    )
    db_session.add(item)
    db_session.commit()
    item_id = item.id

    # User 1 successfully updates item from v1 -> v2
    res_valid = client.patch(
        f"/api/items/{item_id}",
        headers={"Authorization": f"Bearer {token_m1}"},
        json={
            "title": "Updated Title by Member 1",
            "version": 1
        }
    )
    assert res_valid.status_code == 200
    assert res_valid.json()["version"] == 2
    assert res_valid.json()["title"] == "Updated Title by Member 1"

    # User 2 attempts to update using stale version (v1)
    res_stale = client.patch(
        f"/api/items/{item_id}",
        headers={"Authorization": f"Bearer {token_m2}"},
        json={
            "title": "Stale Overwrite Attempt",
            "version": 1
        }
    )
    assert res_stale.status_code == 409, f"Expected 409 Conflict, got {res_stale.status_code}"
    assert "Conflict" in res_stale.json()["detail"]

    # Verify that the database retained User 1's title and did not overwrite with User 2's
    db_session.expire_all()
    fresh = db_session.query(WorkItem).filter(WorkItem.id == item_id).first()
    assert fresh.title == "Updated Title by Member 1"
    assert fresh.version == 2

# 3. Critical Behaviour: Idempotency - Repeated Request (Double-Click / Retry)
def test_idempotency_key_duplicate_request(client, seed_test_context, db_session):
    """
    Repeated request with the same Idempotency-Key header creates exactly ONE item
    and returns identical responses.
    """
    ctx = seed_test_context
    team_a = ctx["teams"]["a"]
    token = ctx["tokens"]["member_a1"]
    idemp_key = "idemp-test-key-abc-12345"

    payload = {
        "title": "Idempotent Creation Test Item",
        "description": "Double click test",
        "item_type": "incident",
        "priority": "urgent",
        "team_id": team_a.id
    }

    # First request
    res1 = client.post(
        "/api/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idemp_key},
        json=payload
    )
    assert res1.status_code == 201
    item_id = res1.json()["id"]

    # Duplicate request (e.g. rapid double-click or network retry)
    res2 = client.post(
        "/api/items",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idemp_key},
        json=payload
    )
    assert res2.status_code == 201
    assert res2.json()["id"] == item_id
    assert res2.json()["title"] == payload["title"]

    # Verify only ONE item exists with this title in the database
    count = db_session.query(WorkItem).filter(WorkItem.title == payload["title"]).count()
    assert count == 1, f"Expected exactly 1 item, found {count}"

# 4. Critical Behaviour: Workflow Rules & Role Enforcement
def test_workflow_state_machine_and_lead_approval(client, seed_test_context, db_session):
    """
    Validates:
    - Invalid jump (Open -> Closed directly) is rejected (400 Bad Request).
    - Legal transition (Open -> In Progress) succeeds (200).
    - Legal transition (In Progress -> Awaiting Approval) succeeds (200).
    - Member attempting Approval (Awaiting Approval -> Resolved) is forbidden (403).
    - Lead attempting Approval (Awaiting Approval -> Resolved) succeeds (200).
    """
    ctx = seed_test_context
    team_a = ctx["teams"]["a"]
    token_member = ctx["tokens"]["member_a1"]
    token_lead = ctx["tokens"]["lead_a"]

    # Create item in 'open' status at v1
    item = WorkItem(
        title="Workflow Lifecycle Item",
        team_id=team_a.id,
        status="open",
        version=1
    )
    db_session.add(item)
    db_session.commit()
    item_id = item.id

    # 1. Invalid jump: Open -> Closed directly
    res_invalid = client.post(
        f"/api/items/{item_id}/transition",
        headers={"Authorization": f"Bearer {token_member}"},
        json={"to_status": "closed", "version": 1}
    )
    assert res_invalid.status_code == 400
    assert "Illegal transition" in res_invalid.json()["detail"]

    # 2. Open -> In Progress (Member allowed)
    res_in_prog = client.post(
        f"/api/items/{item_id}/transition",
        headers={"Authorization": f"Bearer {token_member}"},
        json={"to_status": "in_progress", "version": 1}
    )
    assert res_in_prog.status_code == 200
    assert res_in_prog.json()["status"] == "in_progress"
    assert res_in_prog.json()["version"] == 2

    # 3. In Progress -> Awaiting Approval (Member allowed)
    res_await = client.post(
        f"/api/items/{item_id}/transition",
        headers={"Authorization": f"Bearer {token_member}"},
        json={"to_status": "awaiting_approval", "version": 2}
    )
    assert res_await.status_code == 200
    assert res_await.json()["status"] == "awaiting_approval"
    assert res_await.json()["version"] == 3

    # 4. Awaiting Approval -> Resolved by regular Member (FORBIDDEN - 403)
    res_member_approve = client.post(
        f"/api/items/{item_id}/transition",
        headers={"Authorization": f"Bearer {token_member}"},
        json={"to_status": "resolved", "version": 3}
    )
    assert res_member_approve.status_code == 403
    assert "requires 'lead'" in res_member_approve.json()["detail"].lower()

    # 5. Awaiting Approval -> Resolved by Team Lead (ALLOWED - 200)
    res_lead_approve = client.post(
        f"/api/items/{item_id}/transition",
        headers={"Authorization": f"Bearer {token_lead}"},
        json={"to_status": "resolved", "version": 3, "comment": "Approved following QA signoff"}
    )
    assert res_lead_approve.status_code == 200
    assert res_lead_approve.json()["status"] == "resolved"
    assert res_lead_approve.json()["version"] == 4

# 5. Critical Behaviour: Resource-Level Authorization
def test_resource_level_authorization_blocks_cross_team_access(client, seed_test_context, db_session):
    """
    Every query and action is scoped to the user's teams.
    A user from Team B attempting to view, claim, or update Team A's item gets 403 Forbidden.
    """
    ctx = seed_test_context
    team_a = ctx["teams"]["a"]
    token_outsider = ctx["tokens"]["outsider_b"]  # Belongs ONLY to Team B

    # Item belongs to Team A
    item = WorkItem(
        title="Confidential Team A Work Item",
        team_id=team_a.id,
        status="open",
        version=1
    )
    db_session.add(item)
    db_session.commit()
    item_id = item.id

    # 1. Outsider cannot read item detail
    res_get = client.get(
        f"/api/items/{item_id}",
        headers={"Authorization": f"Bearer {token_outsider}"}
    )
    assert res_get.status_code == 403
    assert "Access denied" in res_get.json()["detail"]

    # 2. Outsider cannot claim item
    res_claim = client.post(
        f"/api/items/{item_id}/claim",
        headers={"Authorization": f"Bearer {token_outsider}"},
        json={"version": 1}
    )
    assert res_claim.status_code == 403

    # 3. Outsider cannot update item
    res_patch = client.patch(
        f"/api/items/{item_id}",
        headers={"Authorization": f"Bearer {token_outsider}"},
        json={"title": "Hacked Title", "version": 1}
    )
    assert res_patch.status_code == 403

# 6. Critical Behaviour: Reliable Async Work (Transactional Outbox & Worker)
def test_reliable_async_work_outbox_idempotence_and_retries(db_session):
    """
    Verifies:
    1. Transactional outbox row created.
    2. Job processed twice produces idempotent result without duplicate side-effect.
    3. Failed job retries, increments attempt counter, and marks as 'failed' after max_attempts.
    """
    PROCESSED_DISPATCHES.clear()

    # 1. Create a successful notification job
    job_success = OutboxJob(
        job_type="notify_new_assignee",
        payload=json.dumps({"item_id": 99, "title": "Database Outage", "assignee_id": 42}),
        status="pending",
        attempts=0,
        max_attempts=3
    )
    db_session.add(job_success)
    db_session.commit()

    # Process first time
    success_first = process_single_job(db_session, job_success)
    assert success_first is True
    assert job_success.status == "completed"
    assert job_success.attempts == 1
    assert "notify_new_assignee:99:42" in PROCESSED_DISPATCHES

    # Simulate reprocessing the same job (idempotence verification)
    job_success.status = "pending"
    db_session.commit()
    success_second = process_single_job(db_session, job_success)
    assert success_second is True
    assert job_success.status == "completed"

    # 2. Verify retry and failure limits on transient error
    job_failing = OutboxJob(
        job_type="simulated_fail",
        payload=json.dumps({"test": "data"}),
        status="pending",
        attempts=0,
        max_attempts=2
    )
    db_session.add(job_failing)
    db_session.commit()

    # Attempt 1: Should fail and return to pending for retry
    res1 = process_single_job(db_session, job_failing)
    assert res1 is False
    assert job_failing.attempts == 1
    assert job_failing.status == "pending"

    # Attempt 2: Reaches max_attempts (2) -> marks as 'failed'
    res2 = process_single_job(db_session, job_failing)
    assert res2 is False
    assert job_failing.attempts == 2
    assert job_failing.status == "failed"
    assert "Simulated transient failure" in job_failing.last_error
