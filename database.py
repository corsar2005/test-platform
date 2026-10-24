from collections.abc import Generator
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

NEON_DATABASE_URL = os.environ.get("NEON_DATABASE_URL")
DATABASE_URL = os.environ.get("DATABASE_URL")
if NEON_DATABASE_URL or DATABASE_URL:
    database_url = make_url(NEON_DATABASE_URL or DATABASE_URL)
    if database_url.get_backend_name() == "postgresql" and database_url.drivername == "postgresql":
        database_url = database_url.set(drivername="postgresql+psycopg")
    if NEON_DATABASE_URL:
        if database_url.get_backend_name() != "postgresql":
            raise ValueError("NEON_DATABASE_URL must be a PostgreSQL connection URL")
        query = dict(database_url.query)
        query.setdefault("sslmode", "require")
        database_url = database_url.set(query=query)
else:
    sqlite_path = Path(os.environ.get("SQLITE_DATABASE_PATH", "test_platform.db"))
    if not sqlite_path.is_absolute() and str(sqlite_path) != ":memory:":
        sqlite_path = PROJECT_DIR / sqlite_path
    database_url = URL.create("sqlite", database=str(sqlite_path))

if database_url.get_backend_name() == "sqlite":
    connect_args = {"check_same_thread": False}
else:
    connect_args = {}

engine = create_engine(
    database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
