import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ProjectStatus(enum.StrEnum):
    PENDING = "pending"
    SCRAPING = "scraping"
    SUMMARIZING = "summarizing"
    PROMPTS_READY = "prompts_ready"
    GENERATING = "generating"
    STITCHING = "stitching"
    COMPLETED = "completed"
    FAILED = "failed"


class SceneStatus(enum.StrEnum):
    PENDING = "pending"
    GENERATING_AUDIO = "generating_audio"
    GENERATING_VIDEO = "generating_video"
    COMPLETED = "completed"
    FAILED = "failed"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.PENDING
    )
    final_video_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    scenes: Mapped[list["ScenePrompt"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ScenePrompt.sequence_order",
    )


class ScenePrompt(Base):
    __tablename__ = "scene_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    narration_text: Mapped[str] = mapped_column(Text, nullable=False)
    video_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    duration_estimate: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[SceneStatus] = mapped_column(Enum(SceneStatus), default=SceneStatus.PENDING)

    project: Mapped["Project"] = relationship(back_populates="scenes")
    video: Mapped["Video | None"] = relationship(
        back_populates="scene", cascade="all, delete-orphan", uselist=False
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene_prompt_id: Mapped[int] = mapped_column(
        ForeignKey("scene_prompts.id"), unique=True, nullable=False
    )
    video_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_frame_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    scene: Mapped["ScenePrompt"] = relationship(back_populates="video")
