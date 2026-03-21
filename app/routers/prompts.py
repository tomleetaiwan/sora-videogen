from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.database import get_db
from app.models import ScenePrompt
from app.templating import templates

router = APIRouter(prefix="/prompts", tags=["prompts"])


async def _load_prompt_with_relations(db: AsyncSession, prompt_id: int) -> ScenePrompt | None:
    result = await db.execute(
        select(ScenePrompt)
        .options(joinedload(ScenePrompt.project), selectinload(ScenePrompt.video))
        .where(ScenePrompt.id == prompt_id)
    )
    return result.scalar_one_or_none()


@router.get("/{prompt_id}", response_class=HTMLResponse)
async def get_prompt(request: Request, prompt_id: int, db: AsyncSession = Depends(get_db)):
    prompt = await _load_prompt_with_relations(db, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return templates.TemplateResponse(
        request=request,
        name="components/prompt_card.html",
        context={"prompt": prompt, "project": prompt.project},
    )


@router.put("/{prompt_id}", response_class=HTMLResponse)
async def update_prompt(request: Request, prompt_id: int, db: AsyncSession = Depends(get_db)):
    prompt = await _load_prompt_with_relations(db, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    form = await request.form()
    if narration := form.get("narration_text"):
        prompt.narration_text = str(narration)
    if video_prompt := form.get("video_prompt"):
        prompt.video_prompt = str(video_prompt)
    if order := form.get("sequence_order"):
        prompt.sequence_order = int(order)

    return templates.TemplateResponse(
        request=request,
        name="components/prompt_card.html",
        context={"prompt": prompt, "project": prompt.project},
    )


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: int, db: AsyncSession = Depends(get_db)):
    prompt = await db.get(ScenePrompt, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    await db.delete(prompt)
    return ""
