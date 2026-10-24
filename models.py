from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class QuestionType(str, Enum):
    SINGLE = "single"
    MULTIPLE = "multiple"
    WRITTEN = "written"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    ADVANCED = "advanced"
    EXPERT = "expert"
    MASTER = "master"


class Subject(str, Enum):
    PYTHON = "python"
    PYTHON_BASES = "python_bases"
    PYTHON_CONDITIONS = "py_conditions"
    PYTHON_STRINGS = "py_strings"
    PYTHON_DATA_STRUCTURES = "py_data"
    PYTHON_LOOPS = "py_loops"
    PYTHON_OOP = "py_oop"
    PYTHON_FUNCTIONS = "py_functions"
    FASTAPI = "fastapi"
    AIOGRAM = "aiogram"
    THREADING = "threading"
    MULTIPROCESSING = "multiprocessing"
    ASYNCIO = "asyncio"
    GENERATOR = "generator"
    DECORATOR = "decorator"
    REGEX = "regex"
    PANDAS = "pandas"
    UNITTEST = "unittest"
    PYTEST = "pytest"
    LINTERS = "linters"
    FILES_FORMATS = "files_formats"
    DATABASES = "databases"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    submissions: Mapped[list["Submission"]] = relationship(back_populates="user")
    article_progress: Mapped[list["ArticleProgress"]] = relationship(back_populates="user")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped[User] = relationship()


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    question_type: Mapped[QuestionType] = mapped_column(String(20), nullable=False)
    subject: Mapped[Subject] = mapped_column(String(20), default=Subject.PYTHON, nullable=False)
    difficulty: Mapped[Difficulty] = mapped_column(String(20), default=Difficulty.MEDIUM, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    starter_code: Mapped[str | None] = mapped_column(Text)
    reference_answer: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    options: Mapped[list["Option"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="Option.position",
    )


class Option(Base):
    __tablename__ = "options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    question: Mapped[Question] = relationship(back_populates="options")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    grade: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    subject: Mapped[str] = mapped_column(String(30), default=Subject.PYTHON.value, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(20), default=Difficulty.MEDIUM.value, nullable=False)
    written_pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped[User | None] = relationship(back_populates="submissions")

    answers: Mapped[list["Answer"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
    )


class ArticleProgress(Base):
    __tablename__ = "article_progress"
    __table_args__ = (UniqueConstraint("user_id", "article_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    article_id: Mapped[str] = mapped_column(String(80), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="article_progress")


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), nullable=False)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False)
    answer_text: Mapped[str | None] = mapped_column(Text)
    selected_option_ids: Mapped[str | None] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    earned_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    submission: Mapped[Submission] = relationship(back_populates="answers")
