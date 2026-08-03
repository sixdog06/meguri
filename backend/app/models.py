"""会话主干的持久化模型。

Conversation = 会话，Message = 消息（见 CONTEXT.md 领域语言）。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        order_by="Message.id",
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    # 结构化负载：assistant 消息携带的工具产出（如 {"seichi": [...]}），无则为 None
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
