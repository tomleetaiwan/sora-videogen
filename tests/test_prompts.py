import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Project, ProjectStatus, ScenePrompt, SceneStatus


@pytest.mark.asyncio
async def test_update_prompt_rejects_risky_video_prompt_and_preserves_saved_value(client, db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.PROMPTS_READY)
        session.add(project)
        await session.flush()
        scene = ScenePrompt(
            project_id=project.id,
            sequence_order=0,
            narration_text="原始旁白",
            video_prompt="A neutral office scene",
            status=SceneStatus.PENDING,
        )
        session.add(scene)
        await session.commit()
        prompt_id = scene.id

    response = await client.put(
        f"/prompts/{prompt_id}",
        data={
            "narration_text": "新的旁白",
            "video_prompt": "Apple store entrance with a clearly visible logo",
            "sequence_order": "0",
        },
    )

    assert response.status_code == 400
    assert "影片提示詞包含後端禁止儲存的高風險描述" in response.text
    assert "Apple store entrance with a clearly visible logo" in response.text

    async with session_factory() as session:
        saved_scene = await session.get(ScenePrompt, prompt_id)

    assert saved_scene is not None
    assert saved_scene.narration_text == "原始旁白"
    assert saved_scene.video_prompt == "A neutral office scene"


@pytest.mark.asyncio
async def test_update_prompt_accepts_safe_video_prompt(client, db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        project = Project(url="https://example.com/article", status=ProjectStatus.PROMPTS_READY)
        session.add(project)
        await session.flush()
        scene = ScenePrompt(
            project_id=project.id,
            sequence_order=0,
            narration_text="原始旁白",
            video_prompt="Old prompt",
            status=SceneStatus.PENDING,
        )
        session.add(scene)
        await session.commit()
        prompt_id = scene.id

    response = await client.put(
        f"/prompts/{prompt_id}",
        data={
            "narration_text": "更新後旁白",
            "video_prompt": "A modern retail lobby without visible branding",
            "sequence_order": "0",
        },
    )

    assert response.status_code == 200
    assert "A modern retail lobby without visible branding" in response.text

    async with session_factory() as session:
        saved_scene = await session.get(ScenePrompt, prompt_id)

    assert saved_scene is not None
    assert saved_scene.narration_text == "更新後旁白"
    assert saved_scene.video_prompt == "A modern retail lobby without visible branding"