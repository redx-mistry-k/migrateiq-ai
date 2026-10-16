from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    value = url or get_settings().database_url
    kwargs = {"pool_pre_ping": True}
    if value.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(value, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def init_db(target_engine=None) -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(target_engine or engine)
