from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PROCESSING_MEDIA = "processing_media"
    TRANSCRIBING = "transcribing"
    ANALYZING_VISUALS = "analyzing_visuals"
    GENERATING_REPORT = "generating_report"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"


STATUS_PROGRESS: Dict[JobStatus, int] = {
    JobStatus.PENDING: 0,
    JobStatus.DOWNLOADING: 15,
    JobStatus.PROCESSING_MEDIA: 30,
    JobStatus.TRANSCRIBING: 55,
    JobStatus.ANALYZING_VISUALS: 70,
    JobStatus.GENERATING_REPORT: 85,
    JobStatus.EXPORTING: 95,
    JobStatus.COMPLETED: 100,
    JobStatus.FAILED: 100,
}

STATUS_MESSAGE: Dict[JobStatus, str] = {
    JobStatus.PENDING: "任务已创建，等待开始",
    JobStatus.DOWNLOADING: "正在下载视频",
    JobStatus.PROCESSING_MEDIA: "正在抽取音频与关键帧",
    JobStatus.TRANSCRIBING: "正在生成逐字稿",
    JobStatus.ANALYZING_VISUALS: "正在分析画面",
    JobStatus.GENERATING_REPORT: "正在生成爆款分析与拆片",
    JobStatus.EXPORTING: "正在导出报告文件",
    JobStatus.COMPLETED: "分析完成",
    JobStatus.FAILED: "任务失败",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Job(BaseModel):
    id: str
    input_url: Optional[str] = None
    source: str = "url"  # url | upload
    status: JobStatus = JobStatus.PENDING
    progress: int = 0
    message: str = STATUS_MESSAGE[JobStatus.PENDING]
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class VideoMetadata(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    duration: Optional[float] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    favorites: Optional[int] = None


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class FrameAnalysis(BaseModel):
    time: float
    frame: str
    ocr_text: Optional[str] = None
    description: Optional[str] = None


class Analysis(BaseModel):
    hook: Optional[str] = None
    conflict: Optional[str] = None
    emotion_value: Optional[str] = None
    info_value: Optional[str] = None
    trust: Optional[str] = None
    share_reason: Optional[str] = None
    comment_trigger: Optional[str] = None
    structure: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    summary: Optional[str] = None


class ClipSuggestion(BaseModel):
    index: int
    start: float
    end: float
    title: str = ""
    hook: str = ""
    opening_subtitle: str = ""
    cover_text: str = ""
    hotspot_type: str = ""
    reason: str = ""
    edit_suggestion: str = ""
    platforms: List[str] = Field(default_factory=list)
    potential: str = ""
    cover_frame: Optional[str] = None


class JobResult(BaseModel):
    job_id: str
    metadata: VideoMetadata = Field(default_factory=VideoMetadata)
    transcript: List[TranscriptSegment] = Field(default_factory=list)
    frames: List[FrameAnalysis] = Field(default_factory=list)
    analysis: Analysis = Field(default_factory=Analysis)
    clips: List[ClipSuggestion] = Field(default_factory=list)
    cover_candidates: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    exports: Dict[str, str] = Field(default_factory=dict)

    def to_public_dict(self) -> Dict[str, Any]:
        return self.model_dump()
