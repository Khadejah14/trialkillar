"""
Cancellation Agent
-------------------
If NOVA_ACT_API_KEY is set in .env → uses Nova Act for real browser automation.
If not set → generates step-by-step cancellation instructions for the user.

This way the app works fully without Nova Act, but upgrades automatically
when the key is added.
"""

import os
import uuid
import logging
from datetime import datetime

from models.schemas import CancellationJob, CancellationStatus

logger = logging.getLogger(__name__)

NOVA_ACT_API_KEY = os.getenv("NOVA_ACT_API_KEY", "").strip()

# Manual cancellation steps per service (fallback when Nova Act not available)
CANCELLATION_STEPS = {
    "netflix": [
        "Go to netflix.com and sign in",
        "Click your profile icon → Account",
        "Under Membership, click 'Cancel Membership'",
        "Click 'Finish Cancellation' to confirm",
    ],
    "spotify": [
        "Go to spotify.com/account and sign in",
        "Click 'Change Plan' under your current plan",
        "Scroll down and click 'Cancel Premium'",
        "Follow the prompts to confirm cancellation",
    ],
    "adobe": [
        "Go to account.adobe.com and sign in",
        "Click 'Manage Plan' next to Creative Cloud",
        "Click 'Cancel Plan' and select a reason",
        "Decline any retention offers and confirm cancellation",
    ],
    "notion": [
        "Go to notion.so and sign in",
        "Click Settings in the sidebar → Plans",
        "Click 'Downgrade' to return to the free plan",
        "Confirm the downgrade",
    ],
    "linkedin": [
        "Go to linkedin.com/premium/manage",
        "Click 'Cancel subscription'",
        "Select a cancellation reason",
        "Click 'Continue to Cancel' and confirm",
    ],
    "canva": [
        "Go to canva.com/settings/purchase",
        "Find your Canva Pro subscription",
        "Click 'Cancel Plan' and follow the steps",
    ],
    "default": [
        "Go to the service's website and sign in",
        "Navigate to Account Settings or Billing",
        "Find your subscription or trial",
        "Click Cancel and follow the confirmation steps",
        "Check your email for a cancellation confirmation",
    ],
}


def _get_steps(service_name: str) -> list[str]:
    key = service_name.lower().split()[0]
    return CANCELLATION_STEPS.get(key, CANCELLATION_STEPS["default"])


def _record(job: CancellationJob, step: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    job.steps_completed.append(f"[{ts}] {step}")


async def run_cancellation_agent(job: CancellationJob) -> CancellationJob:
    """
    Run cancellation — either via Nova Act (if API key set) or manual steps.
    """
    if NOVA_ACT_API_KEY:
        return await _run_nova_act(job)
    else:
        return await _run_manual_instructions(job)


async def _run_nova_act(job: CancellationJob) -> CancellationJob:
    """Real browser automation via Nova Act SDK."""
    job.status = CancellationStatus.IN_PROGRESS
    _record(job, f"Starting Nova Act agent for {job.service_name}")

    try:
        from nova_act import NovaAct, ActError

        with NovaAct(
            starting_page=job.cancellation_url or f"https://www.google.com/search?q={job.service_name}+cancel+subscription",
            nova_act_api_key=NOVA_ACT_API_KEY,
            headless=True,
        ) as nova:
            _record(job, f"Browser opened at {job.cancellation_url}")

            steps = _get_steps(job.service_name)
            for i, instruction in enumerate(steps, 1):
                nova.act(instruction)
                _record(job, f"Step {i}: {instruction}")

            result = nova.act(
                "Is there any confirmation message that the subscription was cancelled?",
                schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}}},
            )
            confirmed = (result.parsed_response or {}).get("confirmed", False)

            if confirmed:
                _record(job, f"✓ Cancellation confirmed by Nova Act")
                job.status = CancellationStatus.SUCCESS
                job.completed_at = datetime.utcnow()
            else:
                _record(job, "⚠ Could not verify — please check the service manually")
                job.status = CancellationStatus.REQUIRES_HUMAN

    except ImportError:
        _record(job, "Nova Act SDK not installed — falling back to manual instructions")
        return await _run_manual_instructions(job)
    except Exception as e:
        _record(job, f"✗ Nova Act error: {e}")
        job.status = CancellationStatus.FAILED
        job.error_message = str(e)

    return job


async def _run_manual_instructions(job: CancellationJob) -> CancellationJob:
    """
    No Nova Act — generate step-by-step instructions for the user.
    Marks the job as REQUIRES_HUMAN so the frontend shows the steps.
    """
    job.status = CancellationStatus.IN_PROGRESS
    _record(job, f"Generating cancellation steps for {job.service_name}")

    steps = _get_steps(job.service_name)
    for i, step in enumerate(steps, 1):
        _record(job, f"Step {i}: {step}")

    if job.cancellation_url:
        _record(job, f"Direct link: {job.cancellation_url}")

    _record(job, "✓ Follow the steps above to complete cancellation")
    _record(job, "Once done, click 'Mark as Cancelled' to update your dashboard")

    job.status = CancellationStatus.REQUIRES_HUMAN
    job.completed_at = datetime.utcnow()
    return job
