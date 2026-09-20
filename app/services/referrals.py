from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, InviteLink, Referral, ReferralMethod, User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_user(session: AsyncSession, tg_user) -> User:
    user = await session.get(User, tg_user.id)
    if user is None:
        user = User(
            user_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
        )
        session.add(user)
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name
    return user


async def upsert_group(session: AsyncSession, chat) -> Group:
    group = await session.get(Group, chat.id)
    if group is None:
        group = Group(group_id=chat.id, title=chat.title)
        session.add(group)
    else:
        group.title = chat.title
    return group


async def get_link_owner(session: AsyncSession, group_id: int, invite_link: str) -> int | None:
    stmt = select(InviteLink.owner_user_id).where(
        InviteLink.group_id == group_id,
        InviteLink.invite_link == invite_link,
        InviteLink.revoked.is_(False),
    )
    return await session.scalar(stmt)


async def get_personal_link(session: AsyncSession, group_id: int, owner_user_id: int) -> InviteLink | None:
    stmt = select(InviteLink).where(
        InviteLink.group_id == group_id,
        InviteLink.owner_user_id == owner_user_id,
        InviteLink.revoked.is_(False),
    )
    return await session.scalar(stmt)


async def save_personal_link(
    session: AsyncSession,
    group_id: int,
    owner_user_id: int,
    invite_link: str,
) -> InviteLink:
    existing = await get_personal_link(session, group_id, owner_user_id)
    if existing:
        existing.invite_link = invite_link
        existing.revoked = False
        return existing

    row = InviteLink(
        group_id=group_id,
        owner_user_id=owner_user_id,
        invite_link=invite_link,
    )
    session.add(row)
    return row


async def record_referral(
    session: AsyncSession,
    *,
    group_id: int,
    invitee_id: int,
    inviter_id: int,
    method: ReferralMethod,
) -> tuple[Referral, bool]:
    existing = await session.scalar(
        select(Referral).where(
            Referral.group_id == group_id,
            Referral.invitee_id == invitee_id,
        )
    )

    if existing:
        existing.active = True
        existing.left_at = None
        existing.join_count += 1
        return existing, False

    referral = Referral(
        group_id=group_id,
        invitee_id=invitee_id,
        inviter_id=inviter_id,
        method=method,
        active=True,
    )
    session.add(referral)
    return referral, True


async def mark_left(session: AsyncSession, group_id: int, invitee_id: int) -> None:
    referral = await session.scalar(
        select(Referral).where(
            Referral.group_id == group_id,
            Referral.invitee_id == invitee_id,
        )
    )
    if referral:
        referral.active = False
        referral.left_at = utcnow()


async def user_stats(session: AsyncSession, group_id: int, inviter_id: int) -> dict[str, int]:
    async def count(*conditions) -> int:
        stmt = select(func.count(Referral.id)).where(
            Referral.group_id == group_id,
            Referral.inviter_id == inviter_id,
            *conditions,
        )
        return int(await session.scalar(stmt) or 0)

    return {
        "total": await count(),
        "active": await count(Referral.active.is_(True)),
        "left": await count(Referral.active.is_(False)),
        "direct": await count(Referral.method == ReferralMethod.DIRECT_ADD),
        "link": await count(Referral.method == ReferralMethod.INVITE_LINK),
    }


async def leaderboard(session: AsyncSession, group_id: int, active_only: bool) -> list[tuple[int, int]]:
    stmt = (
        select(Referral.inviter_id, func.count(Referral.id).label("count"))
        .where(Referral.group_id == group_id)
        .group_by(Referral.inviter_id)
        .order_by(func.count(Referral.id).desc())
        .limit(20)
    )
    if active_only:
        stmt = stmt.where(Referral.active.is_(True))

    result = await session.execute(stmt)
    return [(int(user_id), int(count)) for user_id, count in result.all()]
