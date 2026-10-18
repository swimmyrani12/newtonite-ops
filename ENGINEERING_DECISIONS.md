# Engineering Decisions & Trade-Offs

This document records the architectural context, evaluated options, chosen solutions, and trade-offs for the critical engineering decisions in **Newtonite — Operations Under Pressure**.

---

## 1. Concurrency Control: Optimistic Locking & Conditional Updates vs. Pessimistic Row Locking

### Context
In operational crisis scenarios, multiple engineers frequently attempt to triage or update the same incident ticket simultaneously (e.g., claiming an unassigned server outage, or two responders updating diagnostic notes). Without proper concurrency controls, race conditions result in lost updates (last-write-wins) or double assignment.

### Options Considered
1. **Pessimistic Row Locking (`SELECT ... FOR UPDATE`)**: Locks the database row during read until the transaction finishes.
   - *Pros*: Prevents interleaving updates.
   - *Cons*: Holds open row locks across network requests, increases database connection pool exhaustion under load, prone to deadlocks, and degrades throughput.
2. **Optimistic Concurrency Control (OCC) via Monotonic Versioning**: Every record has an integer `version`. Updates execute conditionally:
   `UPDATE work_items SET ..., version = version + 1 WHERE id = :id AND version = :client_version`
3. **Atomic Conditional Updates for Claims**:
   `UPDATE work_items SET assignee_id = :uid, version = version + 1 WHERE id = :id AND assignee_id IS NULL`

### Chosen Solution
We implemented a hybrid approach:
- **Conditional Atomic Claim**: For unassigned tickets, the atomic conditional update ensures that even under parallel claims, exactly one write modifies a row (`rows_updated == 1`), while subsequent or concurrent writes modify 0 rows and immediately fail with `409 Conflict`.
- **Optimistic Locking**: For item edits and workflow transitions, clients must provide their observed `version`. If another user modified the ticket concurrently, the version check fails (`rows_updated == 0`), throwing `409 Conflict` and providing the latest version to the client.

### Trade-Offs
- *Trade-off*: OCC requires the client to handle rejection gracefully (rolling back optimistic UI updates and presenting a reload action). This trade-off significantly improves system scalability, avoids long-lived database locks, and completely eliminates race hazards.

---

## 2. History & Accountability: Atomic Transactional Coupling of State, Audit Log, and Outbox

### Context
Operational integrity requires an immutable audit trail for every state, priority, and assignee change. If the application updates the work item and writes the audit log in separate transactions, network splits or crashes can leave history disagreeing with the actual state of the item.

### Options Considered
1. **Application-level dual writes**: Update `work_items`, then in a separate try/catch block write to `activity_events` and send an HTTP webhook.
   - *Failure mode*: If the app crashes between writes, the audit log or notification is lost forever.
2. **PostgreSQL triggers / Change Data Capture (CDC)** (e.g., Debezium):
   - *Pros*: Completely decoupled.
   - *Cons*: High operational complexity for a tight sprint; logic buried in database schema rather than testable application code.
3. **Single ACID Transaction (State + Append-Only Audit + Transactional Outbox)**:
   All three writes occur in the exact same database transaction:
   `BEGIN -> Update WorkItem -> Insert ActivityEvent -> Insert OutboxJob -> COMMIT`.

### Chosen Solution
We chose Option 3: **Single ACID Transaction**.
Every mutation route flushes the work item update, appends an `ActivityEvent`, and enqueues an `OutboxJob` inside one single transaction boundary. If any step fails, the entire transaction rolls back cleanly.

### Trade-Offs
- *Trade-off*: Increases transaction write latency by a few milliseconds due to three inserts/updates in one transaction. However, it provides absolute consistency guarantees: history and notification queues can never drift from ticket state.

---

## 3. Workflow State Machine & Resource-Level Authorization

### Context
Security buttons in frontends are cosmetic. In an internal operations platform, engineers belong to different teams (e.g. Infrastructure, Payments, Ops) with varying authorization roles (`Viewer`, `Member`, `Lead`, `Admin`). Transitions (such as approving an operational work item or resolving an incident) must be strictly enforced on the server.

### Options Considered
1. **Ad-hoc route checks**: Each route endpoint manually inspects the user's role and writes `if` statements.
   - *Failure mode*: Inconsistent permission bugs, hard to audit, easy to miss edge cases.
2. **Unified Resource Authorization Matrix & Finite State Machine (FSM)**:
   - A centralized `check_team_permission(user, team_id, required_role)` function executed on every route.
   - A formal state transition graph `ALLOWED_TRANSITIONS = { "open": ["in_progress"], ... }`.
   - Elevated transition gates (`LEAD_ONLY_TRANSITIONS = { ("awaiting_approval", "resolved"), ("resolved", "closed") }`).

### Chosen Solution
Option 2: Centralized server-side FSM and resource authorization.
If a user belongs to Team B, attempting to read, claim, or transition an item from Team A results in `403 Forbidden` at the service layer. Furthermore, even if a Member belongs to the team, attempting to approve an item without `Lead` or `Admin` authority is rejected with `403 Forbidden`.

### Trade-Offs
- *Trade-off*: Strict state machine enforcement prevents shortcuts (e.g., jumping from `Open` directly to `Closed`). While this requires users to follow the full operational lifecycle, it ensures compliance, QA sign-off, and accountability.

---

## 4. Idempotency: Request Key Deduplication & Response Caching

### Context
Operational pressure causes users to double-click submission buttons, or network interruptions cause HTTP client libraries to auto-retry `POST /api/items`. Without idempotency controls, this results in duplicate tickets and duplicate background alerts.

### Options Considered
1. **Client-side button disabling**: Disable button after click.
   - *Failure mode*: Does not protect against network timeout retries, browser reloads, or automated scripts.
2. **Natural Deduplication via DB unique constraints on title**:
   - *Failure mode*: Legitimate distinct incidents may have identical titles (e.g., "High Memory Alert").
3. **Transactional Idempotency Key with Stored Response Cache**:
   Clients submit a unique `Idempotency-Key` header. The server records the key, hash of the payload, HTTP response status, and response body in the `idempotency_keys` table. Repeated requests immediately return the cached response without creating duplicate items.

### Chosen Solution
Option 3: **Idempotency-Key Header with Response Caching**.
The application verifies the key within the database. If already present, it returns the exact serialized result from the initial creation, ensuring 100% deduplication.

### Trade-Offs
- *Trade-off*: Requires storage of response payloads in the database. For operational systems, the storage overhead is trivial compared to the cost of duplicate work items and alert storms.

---

## 5. Scalability: Cursor-Based Pagination vs. Offset/Limit

### Context
Operational queue tables accumulate tens of thousands of items over time. Standard `OFFSET / LIMIT` pagination suffers from $O(N)$ query degradation as offsets increase, and produces skipped/duplicate items when records are inserted or deleted while a user is navigating.

### Options Considered
1. **Offset/Limit (`OFFSET 1000 LIMIT 20`)**:
   - *Cons*: Scans and discards 1,000 index rows; shifts pages if items are created concurrently.
2. **Cursor-Based Pagination on Indexed Columns (`WHERE id < :cursor ORDER BY id DESC LIMIT :limit`)**:
   - *Pros*: Constant-time $O(1)$ seek using the primary key index (`id`), completely immune to concurrent insertions or page drift.

### Chosen Solution
Option 2: **Cursor-Based Pagination**.
The API returns items ordered monotonically by `id DESC` with a `next_cursor` pointing to the lowest ID in the current page.

### Trade-Offs
- *Trade-off*: Cannot jump directly to arbitrary page numbers (e.g., "Jump to page 47"). However, in incident and operational queues, users iterate sequentially through active workloads or filter by state/priority, making cursor pagination vastly superior in performance and stability.

---

# 🎙️ Newtonite Technical Interview: Deep Dive Answers

### Q1: How does the architecture work? (Draw it in 60 seconds)
```
[Browser Dashboard] 
       │ (HTTP: JWT + Idempotency-Key + Version)
       ▼
[FastAPI Gateway] ──► [Resource Auth & State Machine]
       │
       ▼ (Single ACID Transaction)
┌──────────────────────────────────────────────────┐
│ PostgreSQL                                       │
│ 1. work_items      (UPDATE ... version = ver + 1)│
│ 2. activity_events (INSERT audit record)         │
│ 3. outbox_jobs     (INSERT pending notification) │
└──────────────────────────────────────────────────┘
       ▲
       │ (Poll pending jobs & backoff retry)
[Background Outbox Worker] ──► [Idempotent Dispatcher]
```
**60-second summary**: The browser sends requests with JWT auth, an idempotency key, and the record's current version counter. FastAPI validates team membership and state machine transition rules. It executes the work item mutation, append-only audit event, and notification outbox job inside a **single database transaction**. The standalone background worker polls the outbox, executing notifications with exponential backoff retries and idempotent dispatching.

---

### Q2: What happens when things fail?
- **Database is down**: FastAPI health check fails, incoming requests immediately receive `503 Service Unavailable`, and connection pool retries via `pool_pre_ping`.
- **Worker crashes mid-job**: Outbox jobs are tracked with status `processing`. If a worker dies, uncompleted jobs remain in the table and are picked up by the next worker loop or restart, bounded by `max_attempts`.
- **Network retry / Rapid double-click**: The client sends an `Idempotency-Key` header. The server detects the key in the database and returns the cached response without re-executing business logic.
- **Two users colliding on claim**: Conditional SQL `UPDATE work_items SET assignee_id = ? WHERE id = ? AND assignee_id IS NULL`. The first write modifies 1 row and succeeds. The second write modifies 0 rows, rolls back, and returns `409 Conflict`.
- **User editing a stale version**: Update conditional on `WHERE version = :client_version`. Version mismatch returns `409 Conflict`. The UI displays a reload prompt and preserves server truth.

---

### Q3: What assumptions did you make, and which would you check with a product manager first?
1. **Assumption made**: A work item cannot jump directly from `Open` to `Closed`; it must progress through `In Progress -> Awaiting Approval -> Resolved -> Closed`.
   *PM Check*: Are there "triage-as-duplicate" or "wont-fix" items that should be closed immediately from `Open`?
2. **Assumption made**: Only users with the `Lead` or `Admin` role can pass the `Awaiting Approval -> Resolved` stage.
   *PM Check*: Can Leads delegate approval authority to senior Members during off-hours or on-call rotations?
3. **Assumption made**: Seeded users and team assignments are pre-configured; self-serve sign-up is omitted to keep the perimeter strictly controlled.
   *PM Check*: Will future team provisioning be handled via SCIM/Okta SSO or manual admin assignment?

---

### Q4: Which part of the system matters most, and why?
**The Transactional Boundary & State Machine**.
UI polish and external notification services can easily be swapped or improved, but if concurrent requests can corrupt ticket assignment, lose diagnostic updates, or allow unapproved resolutions, the entire operational pipeline becomes untrustworthy. Guaranteeing data consistency under concurrency is the bedrock of operational systems.

---

### Q5: What would change at 10× scale?
1. **Read Replicas**: Direct all search queries, dashboard KPI reads, and cursor pagination queries to PostgreSQL read replicas, reserving the primary database exclusively for atomic state mutations.
2. **Table Partitioning**: Implement range-based partitioning on `activity_events` (e.g. by month) to maintain constant query execution times as historical events scale into tens of millions.
3. **Dedicated Queue (Redis Streams / Kafka)**: Replace outbox table polling with a CDC stream (e.g. Debezium or PostgreSQL logical replication) feeding Redis/Kafka workers for high-frequency async dispatching.
4. **Dedicated Search Engine**: Offload full-text search across titles and multi-megabyte log descriptions to Elasticsearch / OpenSearch or PostgreSQL pgvector/GIN indexes.

---

### Q6: Most important engineering decision made (in 100 words)
*(For submission Form Question #12)*

> Coupling work-item mutations, append-only audit events, and asynchronous outbox jobs inside a single ACID database transaction governed by optimistic concurrency control (`version` counter) and atomic conditional updates (`assignee_id IS NULL`). This guarantees that concurrent claims always yield exactly one winner without row-locking deadlocks, stale edits are rejected with HTTP 409, and the activity history and notification queues can never drift out-of-sync with the work item state, even across catastrophic worker crashes or network retries.
