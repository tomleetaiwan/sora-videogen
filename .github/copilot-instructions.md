# Copilot Instructions for sora-videogen

## Build, Test, Lint

```bash
# Install
pip install -r requirements.txt

# Run app
uvicorn app.main:app --reload

# Run all tests
pytest

# Run a single test file or test
pytest tests/test_scraper.py -v
pytest tests/test_scraper.py::test_scrape_url_extracts_text -v

# Lint and format
ruff check .
ruff format .
```

Requires Python 3.11+, ffmpeg in PATH, and an OpenAI API key in `.env`.

## Architecture

This is a **FastAPI + Jinja2/HTMX** application that converts web articles into narrated video summaries using OpenAI APIs.

### Pipeline Flow

URL → **scraper** → raw text → **summarizer** (GPT-5-mini) → summary → **prompt_generator** (GPT-5-mini) → scene prompts → user edits prompts via web UI → **tts** (OpenAI TTS) → narration audio → **video_generator** (Sora 2) → video segments → **video_stitcher** (ffmpeg) → final video

The pipeline has two phases:
1. **Preparation** (`start_pipeline`): scrape → summarize → generate prompts → stop for user review
2. **Generation** (`start_generation`): user approves prompts → TTS → video → stitch

Both run as `asyncio.create_task` background tasks tracked in `app/tasks/pipeline.py:_running_tasks`.

### Video Continuity

Each video's last frame is extracted via ffmpeg and passed as a reference image to the next Sora 2 generation call, ensuring visual continuity across scenes.

### Key Directories

- `app/services/` — One service per external capability (scraper, summarizer, prompt_generator, tts, video_generator, video_stitcher). Each is a standalone async module.
- `app/routers/` — FastAPI route handlers. Return HTML via Jinja2 templates (not JSON), consumed by HTMX.
- `app/tasks/` — Background pipeline orchestration.
- `app/templates/` — Jinja2 templates. `components/` contains HTMX-swappable partial fragments.
- `media/{project_id}/` — Generated artifacts (audio, video, frames) per project.

## Conventions

- **Language**: All user-facing text (UI, narration, summaries) is in **Traditional Chinese (繁體中文)** with Taiwanese phrasing. Code comments and variable names are in English.
- **Narration pacing**: ~3 characters/second for Taiwanese Mandarin. Each scene's narration must fit within 20 seconds (~60 characters max).
- **Routers return HTML**: All router endpoints use `response_class=HTMLResponse` and return rendered Jinja2 templates. Status endpoints for polling return partial HTML fragments for HTMX swap.
- **Async throughout**: All I/O-bound operations use `async/await`. Database access uses SQLAlchemy async sessions via `get_db` dependency.
- **Service isolation**: Services in `app/services/` are pure functions — they take inputs and return outputs without touching the database. Only `app/tasks/pipeline.py` orchestrates DB writes.
- **Config via pydantic-settings**: All configuration lives in `app/config.py` using `pydantic_settings.BaseSettings`, loaded from `.env`. Access via `from app.config import settings`.
- **Models use mapped_column**: SQLAlchemy models use the 2.0 `Mapped`/`mapped_column` declarative style with type annotations.
- **Testing**: Tests use `pytest-asyncio` with `asyncio_mode = "auto"`. OpenAI API calls are mocked with `unittest.mock.patch`. Integration tests use `httpx.AsyncClient` with ASGI transport against the FastAPI app.
