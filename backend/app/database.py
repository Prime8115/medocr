"""SQLAlchemy engine, session factory, and declarative base."""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# SQLite needs check_same_thread=False for FastAPI's threadpool workers.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

_pool_args = {} if settings.database_url.startswith("sqlite") else {
    # Sized for the request threads plus the scan workers, explicitly rather
    # than by library default, so a burst of uploads queues for a connection
    # instead of failing.
    "pool_size": settings.db_pool_size,
    "max_overflow": settings.db_max_overflow,
    "pool_timeout": settings.db_pool_timeout,
}
engine = create_engine(
    settings.database_url, connect_args=connect_args, pool_pre_ping=True, **_pool_args
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
