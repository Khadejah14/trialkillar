"""
Scheduler Service
-----------------
Runs background jobs using APScheduler.
No AWS Lambda needed — runs in the same Python process.
"""

import logging
import uuid
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from models.schemas import CancellationJob, CancellationStatus, TrialStatus
from agents.cancellation_agent import run_cancellation_agent
from services import storage

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def auto_cancel_urgent_trials():
    """
    Runs every hour.
    Finds queued subscriptions expiring within 24h and fires the agent.
    """
    cutoff = datetime.utcnow() + timedelta(hours=24)
    subs = storage.get_queued_subscriptions()

    for sub in subs:
        if sub.trial_end_date <= cutoff:
            logger.info(f"Auto-cancelling {sub.service_name} for user {sub.user_id}")

            job = CancellationJob(
                id=str(uuid.uuid4()),
                subscription_id=sub.id,
                user_id=sub.user_id,
                service_name=sub.service_name,
                cancellation_url=sub.cancellation_url,
            )
            storage.save_job(job)

            updated = await run_cancellation_agent(job)
            storage.save_job(updated)

            if updated.status == CancellationStatus.SUCCESS:
                storage.update_subscription_status(sub.id, sub.user_id, TrialStatus.CANCELLED)
                logger.info(f"✓ Auto-cancelled {sub.service_name}")


def start_scheduler():
    scheduler.add_job(
        auto_cancel_urgent_trials,
        trigger=IntervalTrigger(hours=1),
        id="auto_cancel_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    scheduler.shutdown(wait=False)
