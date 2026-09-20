# TG Invite Tracker

Telegram bot for tracking **direct member adds** and **personal invite-link joins** across groups and channels.

## Current features — V0.2

- Direct-add tracking
- Personal invite links
- Permanent Telegram user-ID attribution
- Anti-rejoin / duplicate-credit protection
- Active and all-time leaderboards
- Per-user invite statistics
- Interactive inline-button dashboard
- Private-chat stats for users
- Private admin dashboard for tracked groups/channels
- Overall group/channel admin statistics
- Temporary confirmation replies to visible group join/add events

## Core rule

A Telegram account can earn referral credit only once per chat.

```text
UNIQUE(group_id, invitee_id)
```

If an invitee leaves and later rejoins through another person's link, the original inviter remains credited.

## Interactive UI

Open the bot privately with:

```text
/start
```

The dashboard provides buttons for:

```text
📂 My Chats
🛡 Admin Stats
➕ Add to Group
❓ Help
```

After selecting a tracked chat:

```text
📊 My Stats
🔗 My Invite Link
👥 My Invitees
🏆 Leaderboard
🛡 Overall Admin Stats   (admins only)
```

Users therefore do not need to memorize commands.

## Commands

- `/start` — open interactive dashboard
- `/link` — get/create your personal invite link
- `/myinvites` — personal referral statistics
- `/leaderboard` — active-referral leaderboard
- `/leaderboard all` — all-time legitimate referrals
- `/stats` — overall chat statistics for administrators

Commands return interactive buttons so users can continue navigating without typing more commands.

## Private admin statistics

An administrator can open the bot privately:

```text
/start
→ 🛡 Admin Stats
→ choose group/channel
```

The bot verifies the user's current Telegram administrator status before showing overall stats.

The dashboard can show:

- total recorded referrals
- active referrals
- members/subscribers who left
- direct adds
- invite-link joins
- number of inviters
- leaderboard

## Groups vs channels

### Groups / supergroups

Supported:

- direct-add tracking
- personal-link tracking
- visible join/add event confirmations
- personal stats
- leaderboard
- admin stats

### Channels

Supported:

- bot-created personal invite links
- subscriber membership tracking when Telegram sends the corresponding membership update
- personal stats in private chat
- leaderboard
- admin stats

Channel posts can hide the identity of the human administrator. When Telegram exposes an actual user identity, the bot verifies that user as an admin. If a command is posted as the channel identity itself, only aggregate channel statistics can be shown.

## Temporary invite confirmation

For visible Telegram group service messages such as:

```text
John joined the group via invite link
```

or:

```text
Romeo added John
```

the bot replies directly to that event, for example:

```text
✅ Invite recorded for @Romeo via personal link.
```

The bot deletes its confirmation automatically after a short delay.

Telegram bots do not receive per-user group-message read receipts, so deletion cannot happen at the exact instant a particular user reads the message.

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

## Windows setup

Clone the repository:

```powershell
git clone https://github.com/TRomeoPrince/TG-Invite_tracker.git
cd TG-Invite_tracker
```

Create the virtual environment:

```powershell
python -m venv venv
```

For the current PowerShell window, allow activation scripts:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Activate:

```powershell
.\venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Create the environment file:

```powershell
copy .env.example .env
notepad .env
```

Set:

```env
BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE
DATABASE_URL=sqlite+aiosqlite:///./invite_tracker.db
```

Never commit or share your real Telegram bot token.

Run:

```powershell
python -m app.bot
```

### Returning later

A single PowerShell line to enter the project, activate the environment, update, and run:

```powershell
cd C:\Users\USER\TG-Invite_tracker; Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass; .\venv\Scripts\Activate.ps1; git pull; python -m app.bot
```

## Telegram setup

1. Create the bot using BotFather.
2. Add the bot to the target group or channel.
3. Promote it to administrator.
4. Give it permission to manage/invite users through invite links.
5. Start the Python process.
6. Open the bot privately and use `/start`.

## Database

SQLite is the local-development default.

For production, use PostgreSQL by setting an async database URL such as:

```text
postgresql+asyncpg://user:password@host:5432/invite_tracker
```

## Anti-abuse behavior

- Telegram numeric user IDs are the identity key.
- Usernames are display information only.
- One credited referral per invitee per chat.
- Leaving does not erase original attribution.
- Rejoining does not award another referral.
- Returning members become active again under their original inviter.
- Self-referrals are ignored.
- Bot accounts are ignored.
