"""
SQLite Storage Service
-----------------------
Local database file — no cloud, no account, no credit card.
Creates trialguard.db automatically on first run.
"""

import os
import json
import sqlite3
import logging
from datetime import datetime
from typing import Optional
from contextlib import contextmanager

from models.schemas import Subscription, CancellationJob, TrialStatus, CancellationStatus

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "trialguard.db")


@contextmanager
def get_db():
    """Context manager for SQLite connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent reads
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist. Called on startup."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                service_name TEXT NOT NULL,
                plan_name TEXT NOT NULL,
                trial_end_date TEXT NOT NULL,
                monthly_charge REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                cancellation_url TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                email_source TEXT,
                detected_at TEXT NOT NULL,
                cancelled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS cancellation_jobs (
                id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                service_name TEXT NOT NULL,
                cancellation_url TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                steps_completed TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                completed_at TEXT,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                access_token TEXT,
                refresh_token TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_user ON cancellation_jobs(user_id);
        """)
    logger.info(f"Database ready at {DB_PATH}")


# ── Users ──────────────────────────────────────────────────────────────────

def save_user_tokens(user_id: str, access_token: str, refresh_token: Optional[str]):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, access_token, refresh_token, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token
        """, (user_id, access_token, refresh_token, datetime.utcnow().isoformat()))


def get_user_tokens(user_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        return {"access_token": row["access_token"], "refresh_token": row["refresh_token"]}


# ── Subscriptions ──────────────────────────────────────────────────────────

def save_subscription(sub: Subscription):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO subscriptions
                (id, user_id, service_name, plan_name, trial_end_date,
                 monthly_charge, currency, cancellation_url, status,
                 email_source, detected_at, cancelled_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                cancelled_at=excluded.cancelled_at,
                cancellation_url=excluded.cancellation_url
        """, (
            sub.id, sub.user_id, sub.service_name, sub.plan_name,
            sub.trial_end_date.isoformat(), sub.monthly_charge, sub.currency,
            sub.cancellation_url, sub.status.value, sub.email_source,
            sub.detected_at.isoformat(),
            sub.cancelled_at.isoformat() if sub.cancelled_at else None,
        ))


def get_subscriptions(user_id: str) -> list[Subscription]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY trial_end_date ASC",
            (user_id,)
        ).fetchall()

    subs = []
    for row in rows:
        try:
            subs.append(Subscription(
                id=row["id"],
                user_id=row["user_id"],
                service_name=row["service_name"],
                plan_name=row["plan_name"],
                trial_end_date=datetime.fromisoformat(row["trial_end_date"]),
                monthly_charge=row["monthly_charge"],
                currency=row["currency"],
                cancellation_url=row["cancellation_url"] or "",
                status=TrialStatus(row["status"]),
                email_source=row["email_source"],
                detected_at=datetime.fromisoformat(row["detected_at"]),
                cancelled_at=datetime.fromisoformat(row["cancelled_at"]) if row["cancelled_at"] else None,
            ))
        except Exception as e:
            logger.warning(f"Failed to parse subscription row: {e}")
    return subs


def update_subscription_status(sub_id: str, user_id: str, status: TrialStatus):
    cancelled_at = datetime.utcnow().isoformat() if status == TrialStatus.CANCELLED else None
    with get_db() as conn:
        conn.execute(
            "UPDATE subscriptions SET status=?, cancelled_at=? WHERE id=? AND user_id=?",
            (status.value, cancelled_at, sub_id, user_id)
        )


def subscription_exists(user_id: str, service_name: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM subscriptions WHERE user_id=? AND LOWER(service_name)=LOWER(?)",
            (user_id, service_name)
        ).fetchone()
        return row is not None


# ── Cancellation Jobs ──────────────────────────────────────────────────────

def save_job(job: CancellationJob):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO cancellation_jobs
                (id, subscription_id, user_id, service_name, cancellation_url,
                 status, steps_completed, created_at, completed_at, error_message)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                steps_completed=excluded.steps_completed,
                completed_at=excluded.completed_at,
                error_message=excluded.error_message
        """, (
            job.id, job.subscription_id, job.user_id, job.service_name,
            job.cancellation_url, job.status.value,
            json.dumps(job.steps_completed),
            job.created_at.isoformat(),
            job.completed_at.isoformat() if job.completed_at else None,
            job.error_message,
        ))


def get_job(job_id: str, user_id: str) -> Optional[CancellationJob]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM cancellation_jobs WHERE id=? AND user_id=?",
            (job_id, user_id)
        ).fetchone()
    if not row:
        return None
    return CancellationJob(
        id=row["id"],
        subscription_id=row["subscription_id"],
        user_id=row["user_id"],
        service_name=row["service_name"],
        cancellation_url=row["cancellation_url"] or "",
        status=CancellationStatus(row["status"]),
        steps_completed=json.loads(row["steps_completed"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        error_message=row["error_message"],
    )


def get_queued_subscriptions() -> list[Subscription]:
    """Return all queued subscriptions across all users (for scheduler)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE status IN ('queued', 'urgent')"
        ).fetchall()
    subs = []
    for row in rows:
        try:
            subs.append(Subscription(
                id=row["id"], user_id=row["user_id"],
                service_name=row["service_name"], plan_name=row["plan_name"],
                trial_end_date=datetime.fromisoformat(row["trial_end_date"]),
                monthly_charge=row["monthly_charge"], currency=row["currency"],
                cancellation_url=row["cancellation_url"] or "",
                status=TrialStatus(row["status"]),
                detected_at=datetime.fromisoformat(row["detected_at"]),
            ))
        except Exception:
            pass
    return subs
