from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from typing import List

from .config import apply_llm_config, settings
from .db import create_job, get_config, get_job, init_db, list_jobs, save_job, set_config
from .jobs import load_result, reanalyze_job, retry_job, run_job, save_transcript
from .models import Job, JobStatus, TranscriptSegment
from .pipeline.ingest import normalize_url

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="autoCat 抖音自动拆片", version="0.1.0")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

init_db()


def _load_persisted_settings() -> None:
    """启动时用数据库中持久化的配置覆盖环境变量默认值。"""

    cfg = get_config()
    mapping = {}
    if "llm_base_url" in cfg:
        mapping["base_url"] = cfg["llm_base_url"]
    if "llm_api_key" in cfg:
        mapping["api_key"] = cfg["llm_api_key"]
    if "llm_model" in cfg:
        mapping["model"] = cfg["llm_model"]
    if "llm_timeout_seconds" in cfg:
        mapping["timeout_seconds"] = cfg["llm_timeout_seconds"]
    if mapping:
        apply_llm_config(mapping)


_load_persisted_settings()


def _new_job_id() -> str:
    return "job_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")


class CreateJobRequest(BaseModel):
    url: str


class TranscriptUpdateRequest(BaseModel):
    segments: List[TranscriptSegment]


class LLMConfigRequest(BaseModel):
    base_url: str = ""
    api_key: Optional[str] = None
    model: str = ""
    timeout_seconds: int = 120


# ----------------------- 页面路由 -----------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"jobs": list_jobs(20), "tools": _tool_status()},
    )


@app.get("/jobs", response_class=HTMLResponse)
def history_page(request: Request):
    return templates.TemplateResponse(
        request, "history.html", {"jobs": list_jobs(100)}
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {})


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status == JobStatus.COMPLETED:
        return RedirectResponse(url=f"/result/{job_id}", status_code=302)
    return templates.TemplateResponse(request, "job.html", {"job": job})


@app.get("/result/{job_id}", response_class=HTMLResponse)
def result_page(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = load_result(job_id)
    if result is None:
        return RedirectResponse(url=f"/job/{job_id}", status_code=302)
    return templates.TemplateResponse(
        request, "result.html", {"job": job, "result": result}
    )


# ----------------------- API 路由 -----------------------


def _llm_settings_public() -> dict:
    llm = settings.llm
    key = llm.api_key or ""
    if len(key) > 8:
        masked = key[:4] + "****" + key[-4:]
    elif key:
        masked = "****"
    else:
        masked = ""
    return {
        "base_url": llm.base_url or "",
        "model": llm.model,
        "timeout_seconds": llm.timeout_seconds,
        "has_api_key": bool(key),
        "api_key_masked": masked,
        "enabled": llm.enabled,
    }


@app.get("/api/settings/llm")
def api_get_llm_settings():
    return _llm_settings_public()


@app.put("/api/settings/llm")
def api_update_llm_settings(payload: LLMConfigRequest):
    mapping = {
        "base_url": payload.base_url,
        "timeout_seconds": payload.timeout_seconds,
    }
    if payload.model.strip():
        mapping["model"] = payload.model.strip()
    # api_key 省略或留空表示保持原值不变，仅在显式传入非空值时更新
    if payload.api_key is not None and payload.api_key.strip():
        mapping["api_key"] = payload.api_key.strip()
    apply_llm_config(mapping)

    persist = {
        "llm_base_url": settings.llm.base_url or "",
        "llm_model": settings.llm.model,
        "llm_timeout_seconds": settings.llm.timeout_seconds,
    }
    if settings.llm.api_key:
        persist["llm_api_key"] = settings.llm.api_key
    set_config(persist)
    return _llm_settings_public()


@app.post("/api/jobs")
def api_create_job(payload: CreateJobRequest, background: BackgroundTasks):
    url = normalize_url(payload.url) or payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="无法从输入中解析链接")
    job = Job(id=_new_job_id(), input_url=url, source="url", status=JobStatus.PENDING)
    create_job(job)
    background.add_task(run_job, job.id)
    return JSONResponse({"job_id": job.id, "status": job.status.value})


@app.post("/api/jobs/upload")
async def api_upload_job(
    background: BackgroundTasks,
    file: UploadFile = File(...),
):
    job = Job(id=_new_job_id(), input_url=None, source="upload", status=JobStatus.PENDING)
    create_job(job)
    job_dir = settings.job_dir(job.id)
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    target = job_dir / f"video{suffix}"
    with target.open("wb") as fh:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    background.add_task(run_job, job.id)
    return JSONResponse({"job_id": job.id, "status": job.status.value})


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "job_id": job.id,
        "status": job.status.value,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "warnings": job.warnings,
    }


@app.get("/api/jobs/{job_id}/result")
def api_job_result(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = load_result(job_id)
    if result is None:
        raise HTTPException(status_code=409, detail="结果尚未生成")
    return result.to_public_dict()


@app.put("/api/jobs/{job_id}/transcript")
def api_update_transcript(job_id: str, payload: TranscriptUpdateRequest):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    save_transcript(job_id, payload.segments)
    return {"job_id": job_id, "segments": len(payload.segments)}


@app.post("/api/jobs/{job_id}/retry")
def api_retry(job_id: str, background: BackgroundTasks):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    job.status = JobStatus.PENDING
    job.progress = 0
    job.message = "任务已重新排队"
    job.error = None
    job.warnings = []
    save_job(job)
    background.add_task(retry_job, job_id)
    return {"job_id": job_id, "status": JobStatus.PENDING.value}


@app.post("/api/jobs/{job_id}/reanalyze")
def api_reanalyze(job_id: str, background: BackgroundTasks):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    job.status = JobStatus.GENERATING_REPORT
    job.progress = 85
    job.message = "正在基于编辑后的逐字稿重新分析"
    job.error = None
    save_job(job)
    background.add_task(reanalyze_job, job_id)
    return {"job_id": job_id, "status": JobStatus.GENERATING_REPORT.value}


@app.get("/downloads/{job_id}/{filename}")
def download_file(job_id: str, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = settings.job_dir(job_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(path), filename=filename)


@app.get("/downloads/{job_id}/frames/{filename}")
def download_frame(job_id: str, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = settings.job_dir(job_id) / "frames" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="帧文件不存在")
    return FileResponse(str(path))


def _tool_status() -> dict:
    return {
        "ffmpeg": bool(settings.ffmpeg_path),
        "ffprobe": bool(settings.ffprobe_path),
        "ytdlp": bool(settings.ytdlp_path),
        "llm": settings.llm.enabled,
    }
