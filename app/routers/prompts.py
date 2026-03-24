from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.database import get_db
from app.models import ScenePrompt
from app.services.prompt_generator import detect_video_prompt_risks
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
    form_values = {
        "narration_text": str(form.get("narration_text", prompt.narration_text)),
        "video_prompt": str(form.get("video_prompt", prompt.video_prompt)),
        "sequence_order": str(form.get("sequence_order", prompt.sequence_order)),
    }

    video_prompt_risks = detect_video_prompt_risks(form_values["video_prompt"])
    if video_prompt_risks:
        return templates.TemplateResponse(
            request=request,
            name="components/prompt_card.html",
            context={
                "prompt": prompt,
                "project": prompt.project,
                "form_values": form_values,
                "video_prompt_error": (
                    "影片提示詞包含後端禁止儲存的高風險描述："
                    + "、".join(video_prompt_risks)
                    + "。請改成泛化場景，例如「大型科技公司辦公室」或「不出現可辨識品牌的店面」。"
                ),
            },
            status_code=400,
        )

    prompt.narration_text = form_values["narration_text"]
    prompt.video_prompt = form_values["video_prompt"]
    prompt.sequence_order = int(form_values["sequence_order"])

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
