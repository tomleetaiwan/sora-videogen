from pydantic import BaseModel, HttpUrl

# --- 專案 ---

class ProjectCreate(BaseModel):
    url: HttpUrl


class ScenePromptOut(BaseModel):
    id: int
    sequence_order: int
    narration_text: str
    video_prompt: str
    duration_estimate: float | None
    status: str

    model_config = {"from_attributes": True}


class ProjectOut(BaseModel):
    id: int
    url: str
    summary: str | None
    status: str
    final_video_path: str | None
    error_message: str | None
    created_at: str
    scenes: list[ScenePromptOut] = []

    model_config = {"from_attributes": True}


class ProjectListOut(BaseModel):
    id: int
    url: str
    status: str
    created_at: str

    model_config = {"from_attributes": True}


# --- 分鏡提示詞 ---

class ScenePromptUpdate(BaseModel):
    narration_text: str | None = None
    video_prompt: str | None = None
    sequence_order: int | None = None


# --- 影片生成 ---

class GenerationStatus(BaseModel):
    project_id: int
    status: str
    scenes_total: int
    scenes_completed: int
    current_scene: int | None
