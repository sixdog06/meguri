"""持久化模型：会话（Conversation）/消息（Message）/行程快照（Itinerary）/
作品目录（AnimeWorkRow）。领域语言见 CONTEXT.md。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    """UTC 当前时间（字段默认值工厂；不用 naive localtime）。"""
    return datetime.now(timezone.utc)


class Conversation(Base):
    """会话：一次巡礼咨询的对话容器；消息经 relationship 按 id 排序级联。"""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        order_by="Message.id",
        cascade="all, delete-orphan",
    )


class Message(Base):
    """消息：会话中的单条发言（user/assistant）；assistant 可带结构化 payload
    （工具产出，按工具名组织，如 search_seichi 候选、plan_itinerary 快照）。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    # 结构化负载：assistant 消息携带的工具产出（按工具名组织），无则为 None
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Itinerary(Base):
    """行程快照（#5）：一次规划产出的单一结构化文档（天→圣地序列+交通段）。

    与会话关联，每会话可能有多份（重新规划）；读取时取最新一份。
    """

    __tablename__ = "itineraries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id"), index=True
    )
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class AnimeWorkRow(Base):
    """动画作品目录（anime_works 表）：Bangumi 全量动画索引的 DB 服务层，
    是作品名解析（作品名 → subjectID）的数据源。

    数据来源是 Git 里的 data/works/anime-1990plus.json（ingest_bangumi 产物），
    经 app.ingest_works 幂等 upsert 进本表——表是可重建的派生物。
    *_norm 列是去掉空白后的名字（"轻音少女第二季" 与 "轻音少女 第二季"
    等价匹配靠它，trigram 索引建在 norm 列上）。
    """

    __tablename__ = "anime_works"

    subject_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)  # 日文原名
    name_cn: Mapped[str] = mapped_column(Text, default="")
    name_norm: Mapped[str] = mapped_column(Text, default="")
    name_cn_norm: Mapped[str] = mapped_column(Text, default="")
    air_date: Mapped[str] = mapped_column(String(16), default="")

    __table_args__ = (
        Index(
            "anime_works_name_norm_trgm",
            "name_norm",
            postgresql_using="gin",
            postgresql_ops={"name_norm": "gin_trgm_ops"},
        ),
        Index(
            "anime_works_name_cn_norm_trgm",
            "name_cn_norm",
            postgresql_using="gin",
            postgresql_ops={"name_cn_norm": "gin_trgm_ops"},
        ),
    )
