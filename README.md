# TrialGuard Backend v2 — No Cloud Required

Zero AWS. Zero AI APIs. Just Python + Gmail + SQLite.

## Setup (3 steps)

### 1. Install
```bash
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env
# Add your GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
```

### 3. Run
```bash
uvicorn api.main:app --reload --port 8000
```

That's it. SQLite database (`trialguard.db`) is created automatically.

## API Docs
http://localhost:8000/docs

## Want real browser automation?
Add your Nova Act key to `.env`:
```
NOVA_ACT_API_KEY=your_key_here
```
The app automatically upgrades from manual instructions to full automation.
