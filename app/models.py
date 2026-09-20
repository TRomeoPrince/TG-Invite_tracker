from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ReferralMethod(str, Enum):
    DIRECT_ADD = "DIRECT_ADD"
    INVITE_LINK = "INVITE_LINK"


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Group(Base):
    __tablename__ = "groups"

    group_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InviteLink(Base):
    __tablename__ = "invite_links"
    __table_args__ = (
        UniqueConstraint("group_id", "owner_user_id", name="uq_group_link_owner"),
        UniqueConstraint("invite_link", name="uq_invite_link"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.group_id"), index=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), index=True)
    invite_link: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (
        UniqueConstraint("group_id", "invitee_id", name="uq_group_invitee"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.group_id"), index=True)
    invitee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), index=True)
    inviter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), index=True)
    method: Mapped[ReferralMethod] = mapped_column(SqlEnum(ReferralMethod), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    join_count: Mapped[int] = mapped_column(Integer, default=1)
