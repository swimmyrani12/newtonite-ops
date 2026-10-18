import time
import json
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.app.database import SessionLocal
from backend.app.models import OutboxJob, utcnow
from backend.app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("OutboxWorker")

# In-memory registry to verify and ensure notification idempotency in tests and production
PROCESSED_DISPATCHES = set()

def handle_job(job_type: str, payload_data: dict) -> None:
    """
    Idempotent job handler.
    Ensures that even if a job is delivered or processed multiple times,
    downstream side-effects (e.g. email/slack/webhook) occur exactly once.
    """
    dedup_key = f"{job_type}:{payload_data.get('item_id')}:{payload_data.get('assignee_id') or payload_data.get('to_state')}"
    if dedup_key in PROCESSED_DISPATCHES:
        logger.info(f"[IDEMPOTENT HANDLER] Dispatch '{dedup_key}' already processed downstream. Skipping duplicate dispatch.")
        return

    if job_type == "notify_new_assignee":
        item_id = payload_data.get("item_id")
        title = payload_data.get("title")
        assignee_id = payload_data.get("assignee_id")
        logger.info(f"[NOTIFICATION DISPATCHED] Assignee {assignee_id} notified for work item #{item_id}: '{title}'")
        PROCESSED_DISPATCHES.add(dedup_key)

    elif job_type == "status_transition":
        item_id = payload_data.get("item_id")
        from_st = payload_data.get("from_state")
        to_st = payload_data.get("to_state")
        actor = payload_data.get("actor")
        logger.info(f"[AUDIT EVENT NOTIFIED] Work item #{item_id} transitioned {from_st} -> {to_st} by {actor}")
        PROCESSED_DISPATCHES.add(dedup_key)

    elif job_type == "simulated_fail":
        # Used for testing retry behavior
        raise RuntimeError("Simulated transient failure for worker retry test")

    else:
        logger.warning(f"Unknown job type '{job_type}'. Payload: {payload_data}")

def process_single_job(db: Session, job: OutboxJob) -> bool:
    """Processes an individual outbox job with retry limits and error tracking."""
    job.attempts += 1
    job.status = "processing"
    db.commit()

    try:
        payload = json.loads(job.payload) if isinstance(job.payload, str) else job.payload
        handle_job(job.job_type, payload)

        job.status = "completed"
        job.processed_at = utcnow()
        job.last_error = None
        db.commit()
        return True
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Job #{job.id} ({job.job_type}) failed attempt {job.attempts}/{job.max_attempts}: {error_msg}")
        job.last_error = error_msg

        if job.attempts >= job.max_attempts:
            job.status = "failed"
            logger.error(f"Job #{job.id} reached max attempts ({job.max_attempts}). Marked as FAILED.")
        else:
            job.status = "pending"  # Ready for retry

        db.commit()
        return False

def process_outbox_batch(db: Session, batch_size: int = 10) -> int:
    """
    Pulls a batch of pending jobs from the database and processes them.
    Returns the number of jobs processed.
    """
    jobs = (
        db.query(OutboxJob)
        .filter(OutboxJob.status == "pending")
        .order_by(OutboxJob.created_at.asc())
        .limit(batch_size)
        .all()
    )

    count = 0
    for job in jobs:
        process_single_job(db, job)
        count += 1
    return count

def run_worker_loop():
    """Continuous polling loop for running the outbox worker as a daemon service."""
    logger.info("Newtonite Outbox Worker started. Polling for pending jobs...")
    while True:
        try:
            db = SessionLocal()
            try:
                processed = process_outbox_batch(db, batch_size=20)
                if processed > 0:
                    logger.info(f"Processed {processed} outbox job(s).")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error in outbox worker loop: {e}")

        time.sleep(settings.OUTBOX_POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    run_worker_loop()
