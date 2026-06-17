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

from .config import settings
from .db import create_job, get_job, init_db, list_jobs, save_job
from .jobs import load_result, reanalyze_job, run_job, save_transcript
from .models import Job, JobStatus, TranscriptSegment
from .pipeline.ingest import normalize_url

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="autoCat 抖音自动拆片", version="0.1.0")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

init_db()


def _new_job_id() -> str:
    return "job_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")


class CreateJobRequest(BaseModel):
    url: str


class TranscriptUpdateRequest(BaseModel):
    segments: List[TranscriptSegment]


# ----------------------- 页面路由 -----------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "jobs": list_jobs(20),
            "tools": _tool_status(),
        },
    )


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status == JobStatus.COMPLETED:
        return RedirectResponse(url=f"/result/{job_id}", status_code=302)
    return templates.TemplateResponse(
        "job.html", {"request": request, "job": job}
    )


@app.get("/result/{job_id}", response_class=HTMLResponse)
def result_page(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = load_result(job_id)
    if result is None:
        return RedirectResponse(url=f"/job/{job_id}", status_code=302)
    return templates.TemplateResponse(
        "result.html",
        {"request": request, "job": job, "result": result},
    )


# ----------------------- API 路由 -----------------------


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
