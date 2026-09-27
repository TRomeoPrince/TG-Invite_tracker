import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_CANDIDATES = [
    ROOT / "invite_tracker.db",
    ROOT / "invite_tracker",
    ROOT / "invite_tracker.sqlite3",
]
OUT = ROOT / "migration_export"

TABLE_EXPORTS = {
    "TG_Users.csv": (
        """
        SELECT
            CAST(user_id AS TEXT) AS TelegramUserID,
            COALESCE(username, '') AS Username,
            COALESCE(first_name, '') AS FirstName,
            COALESCE(last_name, '') AS LastName
        FROM users
        """,
        ["TelegramUserID", "Username", "FirstName", "LastName"],
    ),
    "TG_Chats.csv": (
        """
        SELECT
            CAST(group_id AS TEXT) AS ChatID,
            COALESCE(title, '') AS Title,
            '' AS ChatType
        FROM groups
        """,
        ["ChatID", "Title", "ChatType"],
    ),
    "TG_InviteLinks.csv": (
        """
        SELECT
            CAST(group_id AS TEXT) AS ChatID,
            CAST(owner_user_id AS TEXT) AS OwnerUserID,
            invite_link AS InviteLink,
            CASE WHEN revoked THEN 'true' ELSE 'false' END AS Revoked
        FROM invite_links
        """,
        ["ChatID", "OwnerUserID", "InviteLink", "Revoked"],
    ),
    "TG_Referrals.csv": (
        """
        SELECT
            CAST(group_id AS TEXT) AS ChatID,
            CAST(invitee_id AS TEXT) AS InviteeID,
            CAST(inviter_id AS TEXT) AS InviterID,
            method AS Method,
            CASE WHEN active THEN 'true' ELSE 'false' END AS Active,
            join_count AS JoinCount
        FROM referrals
        """,
        ["ChatID", "InviteeID", "InviterID", "Method", "Active", "JoinCount"],
    ),
}


def find_db() -> Path:
    for candidate in DB_CANDIDATES:
        if candidate.exists() and candidate.is_file():
            return candidate
    raise SystemExit(
        "Could not find the old SQLite database. Expected one of: "
        + ", ".join(str(p.name) for p in DB_CANDIDATES)
    )


def main() -> None:
    db_path = find_db()
    OUT.mkdir(exist_ok=True)

    print(f"Using SQLite database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    for filename, (query, headers) in TABLE_EXPORTS.items():
        rows = conn.execute(query).fetchall()
        output_path = OUT / filename

        with output_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({header: row[header] for header in headers})

        print(f"{filename}: {len(rows)} rows -> {output_path}")

    conn.close()
    print("\nExport complete.")
    print("Import each CSV into the matching Catalyst Data Store table.")


if __name__ == "__main__":
    main()
