"""
Gmail Scanner — Regex Edition
-------------------------------
No AI API needed. Uses pattern matching to detect free trial emails and
extract service names, expiry dates, and prices.

Detects emails from: Netflix, Spotify, Adobe, Notion, LinkedIn, Canva,
Dropbox, Amazon, Apple, Microsoft, YouTube, Disney+, and many more.
"""

import re
import uuid
import time
import base64
import logging
from datetime import datetime, timedelta
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from models.schemas import Subscription, ScanResult

logger = logging.getLogger(__name__)

# ── Known service patterns ─────────────────────────────────────────────────
# Maps sender domain / email keywords → (service name, cancellation URL, typical price)

KNOWN_SERVICES = {
    "netflix.com":      ("Netflix",            "https://www.netflix.com/cancelplan",           15.49),
    "spotify.com":      ("Spotify",            "https://www.spotify.com/account/subscription", 11.99),
    "adobe.com":        ("Adobe Creative Cloud","https://account.adobe.com/plans",             59.99),
    "notion.so":        ("Notion",             "https://www.notion.so/my-account",             16.00),
    "linkedin.com":     ("LinkedIn Premium",   "https://www.linkedin.com/premium/manage",      39.99),
    "canva.com":        ("Canva Pro",          "https://www.canva.com/settings/purchase",      14.99),
    "dropbox.com":      ("Dropbox",            "https://www.dropbox.com/account/plan",         11.99),
    "apple.com":        ("Apple One",          "https://appleid.apple.com/account/manage",     19.95),
    "amazon.com":       ("Amazon Prime",       "https://www.amazon.com/mc/pipeline/prime",     14.99),
    "microsoft.com":    ("Microsoft 365",      "https://account.microsoft.com/services",       9.99),
    "youtube.com":      ("YouTube Premium",    "https://www.youtube.com/paid_memberships",     13.99),
    "disneyplus.com":   ("Disney+",            "https://www.disneyplus.com/account",           13.99),
    "hulu.com":         ("Hulu",               "https://secure.hulu.com/account",              17.99),
    "grammarly.com":    ("Grammarly",          "https://account.grammarly.com/subscription",   12.00),
    "duolingo.com":     ("Duolingo Plus",      "https://www.duolingo.com/settings/subscription",6.99),
    "figma.com":        ("Figma",              "https://www.figma.com/settings",               12.00),
    "github.com":       ("GitHub Copilot",     "https://github.com/settings/billing",         10.00),
    "zoom.us":          ("Zoom",               "https://zoom.us/billing",                      15.99),
    "slack.com":        ("Slack",              "https://app.slack.com/plans",                  7.25),
    "chatgpt.com":      ("ChatGPT Plus",       "https://chat.openai.com/settings",             20.00),
    "openai.com":       ("ChatGPT Plus",       "https://platform.openai.com/account/billing",  20.00),
}

# ── Trial keyword patterns ──────────────────────────────────────────────────

TRIAL_PATTERNS = [
    r"free trial",
    r"trial period",
    r"trial ends",
    r"trial expir",
    r"your trial",
    r"cancel before",
    r"cancel anytime",
    r"billing (starts|begins|will start)",
    r"charge(d)? after",
    r"subscription starts",
    r"first (payment|charge|billing)",
    r"no charge until",
    r"won't be charged until",
]

# ── Date extraction patterns ────────────────────────────────────────────────

DATE_PATTERNS = [
    # "March 14, 2026" / "Mar 14, 2026"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+(\d{1,2}),?\s+(\d{4})",
    # "14 March 2026"
    r"(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+(\d{4})",
    # "2026-03-14" or "03/14/2026" or "14/03/2026"
    r"(\d{4})-(\d{2})-(\d{2})",
    r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})",
    # "in X days"
    r"in (\d+) days?",
    # "on the Nth" with context
    r"on (?:the )?(\d{1,2})(?:st|nd|rd|th)",
]

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

# ── Price extraction ────────────────────────────────────────────────────────

PRICE_PATTERN = re.compile(
    r"\$\s?(\d+(?:\.\d{2})?)\s*(?:/\s*(?:mo(?:nth)?|month|yr|year))?",
    re.IGNORECASE
)


def _gmail_service(credentials_dict: dict):
    creds = Credentials(
        token=credentials_dict["access_token"],
        refresh_token=credentials_dict.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=__import__("os").getenv("GOOGLE_CLIENT_ID"),
        client_secret=__import__("os").getenv("GOOGLE_CLIENT_SECRET"),
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode_body(payload: dict) -> str:
    body = ""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    elif "multipart" in mime:
        for part in payload.get("parts", []):
            body += _decode_body(part)
    return body


def _is_trial_email(subject: str, body: str) -> bool:
    text = (subject + " " + body[:2000]).lower()
    return any(re.search(p, text) for p in TRIAL_PATTERNS)


def _extract_service(sender: str, subject: str) -> tuple[str, str, float]:
    """Returns (service_name, cancellation_url, monthly_charge)."""
    sender_lower = sender.lower()
    for domain, info in KNOWN_SERVICES.items():
        if domain in sender_lower:
            return info

    # Fallback: guess from subject line
    subject_lower = subject.lower()
    for domain, info in KNOWN_SERVICES.items():
        service_keyword = info[0].lower().split()[0]
        if service_keyword in subject_lower:
            return info

    # Generic fallback
    name = _guess_service_name(sender)
    return (name, "", 0.0)


def _guess_service_name(sender: str) -> str:
    """Extract a readable service name from an email sender string."""
    match = re.search(r'@([\w\-]+)\.', sender)
    if match:
        domain = match.group(1)
        return domain.replace("-", " ").title()
    return "Unknown Service"


def _extract_date(text: str) -> Optional[datetime]:
    """Try to extract a trial end date from email text."""
    text_lower = text.lower()
    now = datetime.utcnow()

    # "in X days"
    m = re.search(r"in (\d+) days?", text_lower)
    if m:
        return now + timedelta(days=int(m.group(1)))

    # "Month DD, YYYY"
    m = re.search(
        r"(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"\.?\s+(\d{1,2}),?\s+(\d{4})",
        text_lower
    )
    if m:
        month = MONTH_MAP.get(m.group(1)[:3])
        if month:
            try:
                return datetime(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                pass

    # "DD Month YYYY"
    m = re.search(
        r"(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)",
        text_lower
    )
    if m:
        month = MONTH_MAP.get(m.group(2)[:3])
        if month:
            year = now.year if now.month <= month else now.year + 1
            try:
                return datetime(year, month, int(m.group(1)))
            except ValueError:
                pass

    # "YYYY-MM-DD"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # "MM/DD/YYYY"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass

    return None


def _extract_price(text: str) -> float:
    matches = PRICE_PATTERN.findall(text)
    if matches:
        prices = [float(p) for p in matches if 1.0 <= float(p) <= 500.0]
        if prices:
            return min(prices)  # Usually the trial/base price is the lowest
    return 0.0


def _extract_plan_name(subject: str, body: str) -> str:
    text = subject + " " + body[:500]
    # Look for plan tier keywords
    for keyword in ["Premium", "Pro", "Plus", "Standard", "Basic", "All Apps",
                    "Individual", "Family", "Team", "Business", "Enterprise"]:
        if keyword.lower() in text.lower():
            return f"{keyword} Plan"
    return "Free Trial"


async def scan_gmail_for_trials(
    user_id: str,
    credentials_dict: dict,
    max_emails: int = 500,
) -> ScanResult:
    """
    Scan Gmail inbox for free trial emails using regex pattern matching.
    No AI API required.
    """
    start = time.time()
    service = _gmail_service(credentials_dict)

    query = (
        "(subject:trial OR subject:subscription OR subject:\"free trial\" "
        "OR subject:billing OR subject:\"cancel anytime\") "
        "newer_than:90d"
    )

    logger.info(f"[{user_id}] Scanning Gmail...")
    response = service.users().messages().list(
        userId="me", q=query, maxResults=max_emails
    ).execute()
    message_refs = response.get("messages", [])
    logger.info(f"[{user_id}] {len(message_refs)} candidate emails found")

    found: list[Subscription] = []

    for ref in message_refs:
        try:
            msg = service.users().messages().get(
                userId="me", id=ref["id"], format="full"
            ).execute()

            headers = {
                h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            subject = headers.get("subject", "")
            sender = headers.get("from", "")
            body = _decode_body(msg.get("payload", {}))
            text = subject + " " + body

            if not _is_trial_email(subject, body):
                continue

            service_name, cancel_url, known_price = _extract_service(sender, subject)
            trial_end = _extract_date(text) or (datetime.utcnow() + timedelta(days=30))
            price = known_price or _extract_price(text)
            plan = _extract_plan_name(subject, body)

            sub = Subscription(
                id=str(uuid.uuid4()),
                user_id=user_id,
                service_name=service_name,
                plan_name=plan,
                trial_end_date=trial_end,
                monthly_charge=price,
                cancellation_url=cancel_url,
                email_source=ref["id"],
            )
            found.append(sub)
            logger.info(f"[{user_id}] Found: {service_name} — expires {trial_end.date()}")

        except Exception as e:
            logger.warning(f"Error processing email {ref['id']}: {e}")

    duration = time.time() - start
    logger.info(f"[{user_id}] Done: {len(found)} trials in {duration:.1f}s")

    return ScanResult(
        user_id=user_id,
        emails_scanned=len(message_refs),
        trials_found=len(found),
        new_trials=found,
        scan_duration_seconds=duration,
    )
