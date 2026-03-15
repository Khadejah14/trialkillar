# TrialKillar 🔪
### Never get charged after a free trial again

A friend of mine signed up for a bunch of free trials at once and was so annoyed having to go into each one, find the settings, click through all the cancellation screens and deal with all the "are you sure?" popups. She still missed a couple and got charged anyway. That's why I built TrialKillar.

TrialKillar scans your Gmail inbox, finds every free trial you've signed up for, and automatically cancels them before the billing date using **Amazon Nova Act** — a browser automation agent that navigates the cancellation flow for you like a real person would.

---

## How It Works

1. You connect your Gmail account via Google OAuth
2. TrialKillar scans your inbox for free trial confirmation emails
3. All detected trials appear on a dashboard with expiry countdowns
4. You can cancel immediately or queue them for auto-cancellation 24 hours before you'd be charged
5. Amazon Nova Act opens the service's cancellation page, clicks through the flow, handles retention popups, and saves a confirmation

---

## Features

- Detects trials from 20+ services including Netflix, Adobe, Spotify, LinkedIn, Notion, Canva, Dropbox, GitHub, Zoom, ChatGPT and more
- Dashboard shows days remaining, monthly charge, and urgency level
- One-click cancellation or fully automated scheduled cancellation
- Nova Act agent handles the entire browser flow — no manual steps needed
- Works without Nova Act too — falls back to step-by-step instructions
- All data stored locally in SQLite, no cloud account needed

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | HTML, CSS, JavaScript |
| Backend | Python, FastAPI |
| Database | SQLite |
| Email Scanning | Gmail API + regex parsing |
| Browser Automation | Amazon Nova Act |
| Scheduler | APScheduler |
| Auth | Google OAuth 2.0 |

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
```
Open `.env` and add your Google OAuth credentials from [console.cloud.google.com](https://console.cloud.google.com):
```
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
```

### 3. Run the backend
```bash
uvicorn api.main:app --reload --port 8000
```

### 4. Open the frontend
Open `trialguard.html` in your browser. No server needed for the frontend.

The SQLite database `trialguard.db` is created automatically on first run.

---

## Enabling Nova Act (Full Automation)

When you have a Nova Act API key, add it to `.env`:
```
NOVA_ACT_API_KEY=your_key_here
```

The app automatically upgrades from manual instructions to full browser automation. No code changes needed.

---

## Project Structure

```
trialkillar/
├── trialguard.html            # Frontend dashboard
├── api/
│   └── main.py                # FastAPI routes
├── agents/
│   └── cancellation_agent.py  # Nova Act browser automation
├── services/
│   ├── gmail_scanner.py       # Gmail scanning + regex parsing
│   ├── storage.py             # SQLite database layer
│   └── scheduler.py           # Auto-cancel scheduler
├── models/
│   └── schemas.py             # Pydantic data models
├── requirements.txt
└── .env.example
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/auth/google` | Start Gmail OAuth flow |
| GET | `/auth/callback` | OAuth callback |
| POST | `/scan` | Scan inbox for trials |
| GET | `/subscriptions/{user_id}` | List tracked trials |
| POST | `/cancel` | Cancel immediately via Nova Act |
| POST | `/queue/{id}` | Schedule auto-cancellation |
| GET | `/jobs/{job_id}` | Poll cancellation job status |

Full interactive docs at `http://localhost:8000/docs`

---

Built for the **Amazon Nova Hackathon 2025**
