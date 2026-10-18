import random
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from backend.app.database import SessionLocal, Base, engine
from backend.app.models import User, Team, Membership, WorkItem, ActivityEvent, Comment, utcnow
from backend.app.auth import hash_password

def seed_database(total_items: int = 250):
    """
    Populates the database with realistic teams, users across diverse roles,
    and a rich corpus of work items to test server-side cursor pagination,
    filtering, concurrency, and authorization.
    """
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()

    try:
        if db.query(Team).count() > 0:
            print("Database already contains data. Skipping seed.")
            return

        print("Seeding teams...")
        teams_data = [
            ("Platform Infrastructure", "Core cloud infrastructure, Kubernetes clusters, and observability"),
            ("Customer Operations", "Customer escalations, tier-2 incidents, and user onboarding"),
            ("Payments & Billing", "Checkout gateways, recurring billing, fraud analysis, and refunds"),
            ("Security & Compliance", "Vulnerability management, SOC2 audits, and data governance")
        ]
        teams = []
        for name, desc in teams_data:
            t = Team(name=name, description=desc)
            db.add(t)
            teams.append(t)
        db.flush()

        print("Seeding demo users...")
        default_pwd = hash_password("password123")
        users_data = [
            ("admin@newtonite.io", "Alice Walker (Admin)", "admin"),
            ("lead.infra@newtonite.io", "Bob Chen (Infra Lead)", "lead"),
            ("lead.ops@newtonite.io", "Carol Davis (Ops Lead)", "lead"),
            ("lead.pay@newtonite.io", "Dan Kumar (Billing Lead)", "lead"),
            ("eng.infra1@newtonite.io", "Eve Patel (Infra Member)", "member"),
            ("eng.infra2@newtonite.io", "Frank Miller (Infra Member)", "member"),
            ("ops.agent1@newtonite.io", "Grace Lee (Ops Member)", "member"),
            ("pay.eng1@newtonite.io", "Henry Wilson (Billing Member)", "member"),
            ("viewer.auditor@newtonite.io", "Iris Scott (Security Auditor)", "viewer"),
            ("viewer.intern@newtonite.io", "Jack Taylor (Engineering Intern)", "viewer")
        ]

        users = {}
        for email, full_name, _ in users_data:
            u = User(email=email, password_hash=default_pwd, full_name=full_name)
            db.add(u)
            users[email] = u
        db.flush()

        print("Assigning team memberships and roles...")
        # Alice is Admin across all teams
        for t in teams:
            db.add(Membership(user_id=users["admin@newtonite.io"].id, team_id=t.id, role="admin"))

        # Platform Infra members
        db.add(Membership(user_id=users["lead.infra@newtonite.io"].id, team_id=teams[0].id, role="lead"))
        db.add(Membership(user_id=users["eng.infra1@newtonite.io"].id, team_id=teams[0].id, role="member"))
        db.add(Membership(user_id=users["eng.infra2@newtonite.io"].id, team_id=teams[0].id, role="member"))
        db.add(Membership(user_id=users["viewer.intern@newtonite.io"].id, team_id=teams[0].id, role="viewer"))

        # Customer Ops members
        db.add(Membership(user_id=users["lead.ops@newtonite.io"].id, team_id=teams[1].id, role="lead"))
        db.add(Membership(user_id=users["ops.agent1@newtonite.io"].id, team_id=teams[1].id, role="member"))
        db.add(Membership(user_id=users["viewer.auditor@newtonite.io"].id, team_id=teams[1].id, role="viewer"))

        # Payments & Billing members
        db.add(Membership(user_id=users["lead.pay@newtonite.io"].id, team_id=teams[2].id, role="lead"))
        db.add(Membership(user_id=users["pay.eng1@newtonite.io"].id, team_id=teams[2].id, role="member"))

        # Security & Compliance members
        db.add(Membership(user_id=users["viewer.auditor@newtonite.io"].id, team_id=teams[3].id, role="viewer"))
        db.add(Membership(user_id=users["lead.infra@newtonite.io"].id, team_id=teams[3].id, role="member"))
        db.flush()

        print(f"Generating {total_items} realistic work items with audit history...")

        item_templates = [
            ("PostgreSQL connection pool exhaustion in cluster-{cluster}", "incident", "urgent"),
            ("Stripe webhook retry storm causing duplicate ledger entries", "payment", "urgent"),
            ("Enterprise customer {customer} reporting 504 Gateway Timeouts", "customer_issue", "urgent"),
            ("Kubernetes node autoscaler thrashing under flash crowd", "incident", "high"),
            ("SOC2 evidence collection for quarterly IAM privilege review", "compliance", "high"),
            ("Recurring subscription renewal failures for European SEPA cards", "payment", "high"),
            ("Implement rate limiting on public authentication endpoint", "task", "high"),
            ("Investigate SSL certificate expiration for internal gateway", "compliance", "medium"),
            ("Upgrade Redis cache cluster to Redis 7.2 with zero-downtime", "task", "medium"),
            ("Customer feedback: CSV export missing multi-currency fields", "customer_issue", "medium"),
            ("Optimize cursor pagination index on work_items table", "task", "medium"),
            ("Audit orphaned IAM service accounts older than 90 days", "compliance", "low"),
            ("Refactor frontend toast notifications to support rollback alert", "task", "low"),
            ("Update operational playbook for Kafka broker disk replacement", "task", "low"),
        ]

        statuses = ["open", "in_progress", "awaiting_approval", "resolved", "closed"]
        status_weights = [0.35, 0.25, 0.15, 0.15, 0.10]

        team_users = {
            teams[0].id: [users["lead.infra@newtonite.io"], users["eng.infra1@newtonite.io"], users["eng.infra2@newtonite.io"]],
            teams[1].id: [users["lead.ops@newtonite.io"], users["ops.agent1@newtonite.io"]],
            teams[2].id: [users["lead.pay@newtonite.io"], users["pay.eng1@newtonite.io"]],
            teams[3].id: [users["admin@newtonite.io"], users["lead.infra@newtonite.io"]]
        }

        now = datetime.now(timezone.utc)
        items_to_add = []
        events_to_add = []

        for i in range(1, total_items + 1):
            tmpl, item_type, default_priority = random.choice(item_templates)
            title = tmpl.format(
                cluster=random.choice(["us-east-1a", "eu-west-1b", "ap-south-1"]),
                customer=random.choice(["AcmeCorp", "FinTechGlobal", "NexusData", "BioHealth"])
            )
            title = f"[{i:04d}] {title}"

            team = random.choice(teams)
            assigned_candidates = team_users.get(team.id, [users["admin@newtonite.io"]])

            # Distribution of status
            status_choice = random.choices(statuses, weights=status_weights, k=1)[0]

            # Priority (urgent items sometimes unassigned)
            if random.random() < 0.2:
                priority = "urgent"
            elif random.random() < 0.4:
                priority = "high"
            elif random.random() < 0.8:
                priority = "medium"
            else:
                priority = "low"

            # Assignee
            if status_choice == "open" and random.random() < 0.5:
                assignee = None  # Unassigned item available for claim!
            else:
                assignee = random.choice(assigned_candidates)

            creator = random.choice(assigned_candidates)
            item_created_at = now - timedelta(days=random.randint(1, 30), hours=random.randint(0, 23))

            item = WorkItem(
                title=title,
                description=f"Detailed investigation for {title}. Check system logs, metrics dashboards, and recent deployments.",
                item_type=item_type,
                priority=priority,
                status=status_choice,
                team_id=team.id,
                assignee_id=assignee.id if assignee else None,
                created_by_id=creator.id,
                due_date=item_created_at + timedelta(days=random.randint(2, 14)),
                version=random.randint(1, 4),
                created_at=item_created_at,
                updated_at=item_created_at + timedelta(hours=random.randint(1, 24))
            )
            items_to_add.append(item)

        db.bulk_save_objects(items_to_add, return_defaults=True)
        db.flush()

        print("Writing activity history for seeded items...")
        for item in items_to_add:
            ev1 = ActivityEvent(
                work_item_id=item.id,
                actor_id=item.created_by_id,
                event_type="created",
                from_state=None,
                to_state="open",
                details="Item initialized in backlog",
                created_at=item.created_at
            )
            events_to_add.append(ev1)

            if item.assignee_id:
                ev2 = ActivityEvent(
                    work_item_id=item.id,
                    actor_id=item.created_by_id,
                    event_type="assigned",
                    from_state="unassigned",
                    to_state=f"Assigned to user #{item.assignee_id}",
                    details="Directly assigned upon creation",
                    created_at=item.created_at + timedelta(minutes=5)
                )
                events_to_add.append(ev2)

            if item.status in ("in_progress", "awaiting_approval", "resolved", "closed"):
                ev3 = ActivityEvent(
                    work_item_id=item.id,
                    actor_id=item.assignee_id or item.created_by_id,
                    event_type="status_changed",
                    from_state="open",
                    to_state=item.status,
                    details=f"Status advanced to {item.status}",
                    created_at=item.updated_at
                )
                events_to_add.append(ev3)

        db.bulk_save_objects(events_to_add)
        db.commit()
        print(f"Successfully seeded {len(teams)} teams, {len(users)} users, and {len(items_to_add)} work items!")

    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_database(total_items=300)
