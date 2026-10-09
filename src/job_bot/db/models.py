from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC: SQLite drops tzinfo, so all stored datetimes are naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    daily_interview: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    filters: Mapped[list["Filter"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class Filter(Base):
    """Strict search fields of one user subscription."""

    __tablename__ = "filters"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    keywords_any: Mapped[list[str]] = mapped_column(JSON, default=list)
    keywords_all: Mapped[list[str]] = mapped_column(JSON, default=list)
    exclude: Mapped[list[str]] = mapped_column(JSON, default=list)
    city: Mapped[str | None] = mapped_column(String(100))
    hh_area_id: Mapped[str | None] = mapped_column(String(20))
    # remote | hybrid | office | None (any)
    schedule: Mapped[str | None] = mapped_column(String(20))
    # hh codes: noExperience | between1And3 | between3And6 | moreThan6 | None (any)
    experience: Mapped[str | None] = mapped_column(String(20))
    salary_min: Mapped[int | None]
    currency: Mapped[str] = mapped_column(String(8), default="RUR")
    sources: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["hh", "habr", "hirify", "telegram"])
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(back_populates="filters", lazy="selectin")


class Vacancy(Base):
    __tablename__ = "vacancies"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        Index("ix_vacancies_content_hash", "content_hash"),
        Index("ix_vacancies_fetched_at", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str | None] = mapped_column(String(200))
    salary_from: Mapped[int | None]
    salary_to: Mapped[int | None]
    currency: Mapped[str | None] = mapped_column(String(8))
    area: Mapped[str | None] = mapped_column(String(200))
    # comma separated subset of remote,hybrid,office
    schedule: Mapped[str | None] = mapped_column(String(50))
    experience: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None]
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(40))

    # filled by the LLM analyzer
    analyzed_at: Mapped[datetime | None]
    is_vacancy: Mapped[bool | None]
    role: Mapped[str | None] = mapped_column(String(200))
    seniority: Mapped[str | None] = mapped_column(String(20))

    requirements: Mapped[list["Requirement"]] = relationship(
        back_populates="vacancy", cascade="all, delete-orphan", lazy="selectin"
    )


class Requirement(Base):
    __tablename__ = "vacancy_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"), index=True)
    skill: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # hard | soft
    level: Mapped[str] = mapped_column(String(10))  # must | nice

    vacancy: Mapped[Vacancy] = relationship(back_populates="requirements")


class Sent(Base):
    __tablename__ = "sent"
    __table_args__ = (UniqueConstraint("user_id", "vacancy_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    filter_id: Mapped[int | None] = mapped_column(ForeignKey("filters.id", ondelete="SET NULL"))
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"))
    sent_at: Mapped[datetime] = mapped_column(default=utcnow)


class TgChannel(Base):
    __tablename__ = "tg_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    last_message_id: Mapped[int] = mapped_column(default=0)


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    filter_id: Mapped[int | None] = mapped_column(ForeignKey("filters.id", ondelete="SET NULL"))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SourceState(Base):
    """Last successful fetch time for each (source, query) pair."""

    __tablename__ = "source_state"
    __table_args__ = (UniqueConstraint("source", "query_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    query_key: Mapped[str] = mapped_column(String(40))
    last_run_at: Mapped[datetime]
