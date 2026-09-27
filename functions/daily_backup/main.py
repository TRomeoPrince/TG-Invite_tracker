from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import zipfile
from datetime import datetime, timezone

import requests
import zcatalyst_sdk

logger = logging.getLogger()

TABLES = {
    "TG_Users": ["TelegramUserID", "Username", "FirstName", "LastName"],
    "TG_Chats": ["ChatID", "Title", "ChatType"],
    "TG_InviteLinks": ["ChatID", "OwnerUserID", "InviteLink", "Revoked"],
    "TG_Referrals": [
        "ChatID",
        "InviteeID",
        "InviterID",
        "Method",
        "Active",
        "JoinCount",
    ],
    "TG_ChatSettings": ["ChatID", "Enabled", "Message"],
    "TG_Contests": ["ChatID", "Active", "Name", "Prize", "Generation"],
    "TG_ContestReferrals": [
        "ChatID",
        "Generation",
        "InviteeID",
        "InviterID",
        "Active",
    ],
}


def _flatten(item: dict, table: str) -> dict:
    if table in item and isinstance(item[table], dict):
        return item[table]
    return item


def _select_all(app, table: str) -> list[dict]:
    rows = app.zcql().execute_query(f"SELECT * FROM {table}")
    return [_flatten(row, table) for row in rows]


def _build_zip(app) -> tuple[bytes, str, dict[str, int]]:
    now = datetime.now(timezone.utc)
    filename = f"InviterTracker_Backup_{now.strftime('%Y-%m-%d_%H-%M-%S_UTC')}.zip"
    buffer = io.BytesIO()
    counts: dict[str, int] = {}

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for table, columns in TABLES.items():
            rows = _select_all(app, table)
            counts[table] = len(rows)

            text = io.StringIO()
            writer = csv.DictWriter(text, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in columns})

            archive.writestr(
                f"{table}.csv",
                text.getvalue().encode("utf-8-sig"),
            )

        manifest = {
            "app": "Inviter Tracker",
            "created_utc": now.isoformat(),
            "format_version": 1,
            "tables": counts,
        }
        archive.writestr(
            "MANIFEST.json",
            json.dumps(manifest, indent=2).encode("utf-8"),
        )

    return buffer.getvalue(), filename, counts


def _upload_to_drive_webhook(file_bytes: bytes, filename: str, counts: dict[str, int]):
    webhook_url = os.environ.get("BACKUP_WEBHOOK_URL", "").strip()
    secret = os.environ.get("BACKUP_SECRET", "").strip()

    if not webhook_url:
        raise RuntimeError("BACKUP_WEBHOOK_URL is not configured.")
    if not secret:
        raise RuntimeError("BACKUP_SECRET is not configured.")

    payload = {
        "secret": secret,
        "filename": filename,
        "mime_type": "application/zip",
        "data_base64": base64.b64encode(file_bytes).decode("ascii"),
        "metadata": {
            "tables": counts,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        },
    }

    response = requests.post(webhook_url, json=payload, timeout=90)
    response.raise_for_status()

    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Backup receiver rejected upload: {body}")

    return body


def handler(cron_details, context):
    try:
        app = zcatalyst_sdk.initialize()
        backup_bytes, filename, counts = _build_zip(app)
        result = _upload_to_drive_webhook(backup_bytes, filename, counts)
        logger.info("Backup uploaded successfully: %s", result)
        context.close_with_success()
    except Exception as exc:
        logger.exception("Daily backup failed: %s", exc)
        context.close_with_failure()
