# Catalyst Data Store setup

Before deploying the hosted Telegram webhook, create these seven tables in **Catalyst Console → Cloud Scale → Data Store**.

## 1. TG_Users

| Column | Type |
| --- | --- |
| TelegramUserID | Text |
| Username | Text |
| FirstName | Text |
| LastName | Text |

## 2. TG_Chats

| Column | Type |
| --- | --- |
| ChatID | Text |
| Title | Text |
| ChatType | Text |

## 3. TG_InviteLinks

| Column | Type |
| --- | --- |
| ChatID | Text |
| OwnerUserID | Text |
| InviteLink | Text |
| Revoked | Boolean |

## 4. TG_Referrals

| Column | Type |
| --- | --- |
| ChatID | Text |
| InviteeID | Text |
| InviterID | Text |
| Method | Text |
| Active | Boolean |
| JoinCount | Number |

Do not create your own ROWID column. Catalyst adds ROWID automatically.

## Bot token

The hosted function reads the Telegram token from an environment variable named:

```text
BOT_TOKEN
```

Do not commit the token to GitHub or paste it into `main.py`.

## Deployment flow

After the tables exist and BOT_TOKEN is configured for the function:

```cmd
catalyst deploy
```

Catalyst will print the deployed Advanced I/O function URL. Use that HTTPS URL as the Telegram webhook endpoint.

The hosted function accepts:

- GET: health check
- POST: Telegram webhook updates


## 5. TG_ChatSettings

| Column | Type |
| --- | --- |
| ChatID | Text |
| Enabled | Boolean |
| Message | Text |

Recommended defaults:

- `Enabled` → `true`
- `Message` → leave blank; the bot falls back to `👋 HI, {USER}! How are you?`

This table stores per-group welcome-message settings. Channels do not use welcome messages.


## 6. TG_Contests

| Column | Type |
| --- | --- |
| ChatID | Text |
| Active | Boolean |
| Name | Text |
| Prize | Text |
| Generation | Number |

Recommended defaults:

- `Active` → `false`
- `Name` → leave blank; the bot falls back to `Invite Contest`
- `Prize` → leave blank
- `Generation` → `0`

A new generation is created each time an admin starts a new contest. There is no member-count threshold.

## 7. TG_ContestReferrals

| Column | Type |
| --- | --- |
| ChatID | Text |
| Generation | Number |
| InviteeID | Text |
| InviterID | Text |
| Active | Boolean |

This table keeps contest referrals separate from the all-time referral history. If an invitee leaves, their contest referral becomes inactive rather than being duplicated.
