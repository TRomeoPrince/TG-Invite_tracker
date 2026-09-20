# TG Invite Tracker

Telegram bot for tracking **direct member adds** and **personal invite-link joins** in groups.

## V0.1 goals

- Direct-add tracking
- Personal invite links
- Permanent Telegram user-ID attribution
- Anti-rejoin / duplicate-credit protection
- Active and all-time leaderboards
- Per-user invite statistics

## Core rule

A Telegram account can earn referral credit only once per group.

The database enforces:

```text
UNIQUE(group_id, invitee_id)
```

If an invitee leaves and later rejoins through another person's link, the original inviter remains the credited inviter.

## Project structure

```text
TG-Invite_tracker/
├── app/
│   ├── __init__.py
│   ├── bot.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── referrals.py
│   └── handlers/
│       ├── __init__.py
│       ├── commands.py
│       └── members.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Setup

1. Create a bot with **@BotFather** and copy its token.
2. Add the bot to your Telegram group.
3. Promote it to administrator with permission to manage invite links.
4. Clone the repository and create a virtual environment.
5. Install dependencies:

```bash
pip install -r requirements.txt
```

6. Copy `.env.example` to `.env` and add your bot token.
7. Start the bot:

```bash
python -m app.bot
```

Local development uses SQLite by default. For deployment, set `DATABASE_URL` to an async PostgreSQL URL.

## Commands

- `/start` — help/status
- `/link` — create or retrieve your personal group invite link
- `/myinvites` — show your referral statistics
- `/leaderboard` — active-referral leaderboard
- `/leaderboard all` — all-time legitimate-referral leaderboard

## Anti-abuse rules

- Numeric Telegram user IDs are the identity key.
- One credited referral per invitee per group.
- Leaving does not erase original attribution.
- Rejoining does not award another point.
- A returning member is marked active again.
- Self-referrals are ignored.
- Bot accounts are ignored.

## Status

V0.1 foundation.
