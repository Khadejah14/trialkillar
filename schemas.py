from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class TrialStatus(str, Enum):
    ACTIVE = "active"
    URGENT = "urgent"
    QUEUED = "queued"
    CANCELLED = "cancelled"


class CancellationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    REQUIRES_HUMAN = "requires_human"


class Subscription(BaseModel):
    id: str
    user_id: str
    service_name: str
    plan_name: str
    trial_end_date: datetime
    monthly_charge: float
    currency: str = "USD"
    cancellation_url: str = ""
    status: TrialStatus = TrialStatus.ACTIVE
    email_source: Optional[str] = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    cancelled_at: Optional[datetime] = None

    @property
    def days_remaining(self) -> int:
        delta = self.trial_end_date - datetime.utcnow()
        return max(0, delta.days)

    @property
    def computed_status(self) -> TrialStatus:
        if self.status in (TrialStatus.CANCELLED, TrialStatus.QUEUED):
            return self.status
        if self.days_remaining <= 3:
            return TrialStatus.URGENT
        return TrialStatus.ACTIVE


class CancellationJob(BaseModel):
    id: str
    subscription_id: str
    user_id: str
    service_name: str
    cancellation_url: str = ""
    status: CancellationStatus = CancellationStatus.PENDING
    steps_completed: list[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class ScanResult(BaseModel):
    user_id: str
    emails_scanned: int
    trials_found: int
    new_trials: list[Subscription]
    scan_duration_seconds: float
    scanned_at: datetime = Field(default_factory=datetime.utcnow)


class QueueCancellationRequest(BaseModel):
    subscription_id: str
    user_id: str


class CancellationResponse(BaseModel):
    job_id: str
    subscription_id: str
    status: CancellationStatus
    steps_completed: list[str]
    message: str


class SubscriptionListResponse(BaseModel):
    subscriptions: list[Subscription]
    total_potential_charges: float
    urgent_count: int


class ScanRequest(BaseModel):
    user_id: str
    max_emails: int = 500
