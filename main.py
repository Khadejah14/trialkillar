"""
TrialGuard API — SQLite + Regex Edition
========================================
No AWS. No AI API. Just Python + Gmail + SQLite.

Run with: uvicorn api.main:app --reload --port 8000
"""

import os
import uuid
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

from models.schemas import (
    Subscription, CancellationJob, CancellationStatus, TrialStatus,
    ScanRequest, QueueCancellationRequest,
    CancellationResponse, SubscriptionListResponse,
)
from services.gmail_scanner import scan_gmail_for_trials
from agents.cancellation_agent import run_cancellation_agent
from services import storage
from services.scheduler import start_scheduler, stop_scheduler

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

GOOGLE_CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()          # Creates trialguard.db if it doesn't exist
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="TrialGuard API",
    description="Auto-cancel free trials — SQLite + Regex, no cloud required",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # Open for local development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "storage": "SQLite (local)",
        "ai": "regex pattern matching",
        "nova_act": bool(os.getenv("NOVA_ACT_API_KEY")),
    }


# ── Auth ───────────────────────────────────────────────────────────────────

@app.post("/auth/google")
async def start_google_auth():
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=GMAIL_SCOPES)
    flow.redirect_uri = GOOGLE_CLIENT_CONFIG["web"]["redirect_uris"][0]
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return {"auth_url": auth_url, "state": state}


@app.get("/auth/callback")
async def google_auth_callback(code: str, state: str):
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=GMAIL_SCOPES, state=state)
    flow.redirect_uri = GOOGLE_CLIENT_CONFIG["web"]["redirect_uris"][0]
    flow.fetch_token(code=code)

    creds = flow.credentials
    user_id = str(uuid.uuid4())

    # Save tokens to SQLite
    storage.save_user_tokens(
        user_id=user_id,
        access_token=creds.token,
        refresh_token=creds.refresh_token,
    )

    # Redirect back to frontend with user_id
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5500")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"{frontend_url}/trialguard.html?user_id={user_id}")


# ── Scan ───────────────────────────────────────────────────────────────────

@app.post("/scan")
async def scan_inbox(request: ScanRequest):
    tokens = storage.get_user_tokens(request.user_id)
    if not tokens:
        raise HTTPException(status_code=401, detail="Gmail not connected. Please authenticate.")

    try:
        result = await scan_gmail_for_trials(
            user_id=request.user_id,
            credentials_dict=tokens,
            max_emails=request.max_emails,
        )
    except Exception as e:
        logger.exception("Scan failed")
        raise HTTPException(status_code=500, detail=str(e))

    # Save only new subscriptions
    new_count = 0
    for sub in result.new_trials:
        if not storage.subscription_exists(request.user_id, sub.service_name):
            storage.save_subscription(sub)
            new_count += 1

    return {
        "emails_scanned": result.emails_scanned,
        "trials_found": result.trials_found,
        "new_saved": new_count,
        "scan_duration_seconds": round(result.scan_duration_seconds, 2),
        "new_trials": [s.model_dump(mode="json") for s in result.new_trials],
    }


# ── Subscriptions ──────────────────────────────────────────────────────────

@app.get("/subscriptions/{user_id}", response_model=SubscriptionListResponse)
async def list_subscriptions(user_id: str):
    subs = storage.get_subscriptions(user_id)

    for sub in subs:
        computed = sub.computed_status
        if computed != sub.status:
            sub.status = computed
            storage.save_subscription(sub)

    total = sum(
        s.monthly_charge for s in subs
        if s.status not in (TrialStatus.CANCELLED,)
    )
    urgent = sum(1 for s in subs if s.status == TrialStatus.URGENT)

    return SubscriptionListResponse(
        subscriptions=subs,
        total_potential_charges=round(total, 2),
        urgent_count=urgent,
    )


@app.delete("/subscriptions/{subscription_id}")
async def remove_subscription(subscription_id: str, user_id: str):
    storage.update_subscription_status(subscription_id, user_id, TrialStatus.CANCELLED)
    return {"message": "Removed"}


# ── Cancel ─────────────────────────────────────────────────────────────────

@app.post("/cancel", response_model=CancellationResponse)
async def cancel_subscription(request: QueueCancellationRequest, background_tasks: BackgroundTasks):
    subs = storage.get_subscriptions(request.user_id)
    sub = next((s for s in subs if s.id == request.subscription_id), None)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if sub.status == TrialStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Already cancelled")

    job = CancellationJob(
        id=str(uuid.uuid4()),
        subscription_id=sub.id,
        user_id=request.user_id,
        service_name=sub.service_name,
        cancellation_url=sub.cancellation_url,
    )
    storage.save_job(job)

    async def _run():
        updated = await run_cancellation_agent(job)
        storage.save_job(updated)
        if updated.status == CancellationStatus.SUCCESS:
            storage.update_subscription_status(sub.id, request.user_id, TrialStatus.CANCELLED)

    background_tasks.add_task(_run)

    return CancellationResponse(
        job_id=job.id,
        subscription_id=sub.id,
        status=CancellationStatus.PENDING,
        steps_completed=[],
        message=f"Agent started for {sub.service_name}. Poll /jobs/{job.id} for progress.",
    )


@app.post("/queue/{subscription_id}")
async def queue_auto_cancel(subscription_id: str, user_id: str):
    storage.update_subscription_status(subscription_id, user_id, TrialStatus.QUEUED)
    return {"message": "Queued for auto-cancellation"}


@app.post("/subscriptions/{subscription_id}/mark-cancelled")
async def mark_cancelled(subscription_id: str, user_id: str):
    """Let user manually confirm they cancelled a subscription."""
    storage.update_subscription_status(subscription_id, user_id, TrialStatus.CANCELLED)
    return {"message": "Marked as cancelled"}


# ── Jobs ───────────────────────────────────────────────────────────────────

@app.get("/jobs/{job_id}", response_model=CancellationResponse)
async def get_job_status(job_id: str, user_id: str):
    job = storage.get_job(job_id, user_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    msgs = {
        CancellationStatus.PENDING: "Starting...",
        CancellationStatus.IN_PROGRESS: f"Working... ({len(job.steps_completed)} steps done)",
        CancellationStatus.SUCCESS: f"✓ {job.service_name} cancelled!",
        CancellationStatus.FAILED: f"✗ Failed: {job.error_message}",
        CancellationStatus.REQUIRES_HUMAN: "Follow the steps shown to complete cancellation",
    }

    return CancellationResponse(
        job_id=job.id,
        subscription_id=job.subscription_id,
        status=job.status,
        steps_completed=job.steps_completed,
        message=msgs.get(job.status, ""),
    )
