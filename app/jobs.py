from __future__ import annotations

import traceback
from pathlib import Path
from typing import List, Optional

from .config import Settings, settings as default_settings
from .db import get_job, save_job
from .models import (
    Analysis,
    ClipSuggestion,
    FrameAnalysis,
    Job,
    JobResult,
    JobStatus,
    STATUS_MESSAGE,
    STATUS_PROGRESS,
    TranscriptSegment,
    VideoMetadata,
)
from .pipeline import analyze as analyze_mod
from .pipeline import asr as asr_mod
from .pipeline import export as export_mod
from .pipeline import ingest as ingest_mod
from .pipeline import media as media_mod
from .pipeline import vision as vision_mod
from .pipeline.utils import PipelineError, dedupe_warnings


class JobRunner:
    """单任务流水线编排器，按状态机推进并持久化进度。"""

    def __init__(self, job: Job, settings: Settings = default_settings) -> None:
        self.job = job
        self.settings = settings
        self.job_dir: Path = settings.job_dir(job.id)
        self.warnings: List[str] = list(job.warnings)
        self.video_path: Optional[Path] = None
        self.metadata = VideoMetadata(url=job.input_url)

    # ----------------------- 状态推进 -----------------------

    def _set_status(self, status: JobStatus, message: Optional[str] = None) -> None:
        self.job.status = status
        self.job.progress = STATUS_PROGRESS.get(status, self.job.progress)
        self.job.message = message or STATUS_MESSAGE.get(status, "")
        self.job.warnings = dedupe_warnings(self.warnings)
        save_job(self.job)

    def _warn(self, items: List[str]) -> None:
        for item in items:
            if item:
                self.warnings.append(item)

    # ----------------------- 主流程 -----------------------

    def run(self) -> JobResult:
        try:
            self._stage_download()
            self._stage_media()
            transcript = self._stage_transcribe()
            frames = self._stage_visuals()
            analysis, clips = self._stage_report(transcript, frames)
            result = self._stage_export(transcript, frames, analysis, clips)
            self._set_status(JobStatus.COMPLETED)
            return result
        except PipelineError as exc:
            self._fail(str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底，避免后台任务静默失败
            self._fail(f"未预期错误：{exc}\n{traceback.format_exc()[:800]}")
            raise

    def _fail(self, message: str) -> None:
        self.job.error = message
        self.job.status = JobStatus.FAILED
        self.job.progress = 100
        self.job.message = STATUS_MESSAGE[JobStatus.FAILED]
        self.job.warnings = dedupe_warnings(self.warnings)
        save_job(self.job)

    # ----------------------- 各阶段 -----------------------

    def _stage_download(self) -> None:
        if self.job.source == "upload":
            uploaded = _find_uploaded(self.job_dir)
            if uploaded is None:
                raise PipelineError("上传任务缺少视频文件")
            self.video_path, self.metadata = ingest_mod.register_uploaded_video(
                uploaded, self.job_dir, self.settings
            )
            self._set_status(JobStatus.DOWNLOADING, "已接收上传视频")
            return

        self._set_status(JobStatus.DOWNLOADING)
        url = self.job.input_url or ""
        video_path, metadata, warnings = ingest_mod.download_video(
            url, self.job_dir, self.settings
        )
        self.video_path = video_path
        self.metadata = metadata
        self._warn(warnings)

    def _stage_media(self) -> None:
        self._set_status(JobStatus.PROCESSING_MEDIA)
        assert self.video_path is not None
        info = media_mod.probe_media(self.video_path, self.settings)
        if self.metadata.duration is None:
            self.metadata.duration = media_mod.duration_from_probe(info)
        self.audio_path = media_mod.extract_audio(self.video_path, self.settings)
        frames, warnings = media_mod.extract_keyframes(self.video_path, self.settings)
        self.frame_paths = frames
        self._warn(warnings)
        scenes, scene_warnings = media_mod.detect_scene_changes(
            self.video_path, self.settings
        )
        self.scene_changes = scenes
        self._warn(scene_warnings)

    def _stage_transcribe(self) -> List[TranscriptSegment]:
        self._set_status(JobStatus.TRANSCRIBING)
        try:
            segments, warnings = asr_mod.transcribe(self.audio_path, self.settings)
            self._warn(warnings)
        except PipelineError as exc:
            if self.settings.analysis.continue_on_partial_failure:
                self._warn([f"转写失败，已基于空文稿继续：{exc}"])
                segments = []
            else:
                raise
        asr_mod.write_transcript(segments, self.job_dir)
        asr_mod.write_srt(segments, self.job_dir)
        return segments

    def _stage_visuals(self) -> List[FrameAnalysis]:
        self._set_status(JobStatus.ANALYZING_VISUALS)
        frames, warnings = vision_mod.analyze_frames(self.frame_paths, self.settings)
        self._warn(warnings)
        vision_mod.write_frames(frames, self.job_dir)
        return frames

    def _stage_report(
        self, transcript: List[TranscriptSegment], frames: List[FrameAnalysis]
    ):
        self._set_status(JobStatus.GENERATING_REPORT)
        analysis, clips, warnings = analyze_mod.analyze(
            self.metadata,
            transcript,
            frames,
            self.settings,
            scene_changes=getattr(self, "scene_changes", None),
        )
        self._warn(warnings)
        return analysis, clips

    def _stage_export(
        self,
        transcript: List[TranscriptSegment],
        frames: List[FrameAnalysis],
        analysis: Analysis,
        clips: List[ClipSuggestion],
    ) -> JobResult:
        self._set_status(JobStatus.EXPORTING)
        cover_candidates = vision_mod.select_cover_candidates(frames, clips, self.job.id)
        result = JobResult(
            job_id=self.job.id,
            metadata=self.metadata,
            transcript=transcript,
            frames=frames,
            analysis=analysis,
            clips=clips,
            cover_candidates=cover_candidates,
            warnings=dedupe_warnings(self.warnings),
        )
        export_mod.export_markdown(result, self.job_dir)
        xlsx_path = export_mod.export_xlsx(result, self.job_dir)
        if xlsx_path is None:
            self._warn(["未安装 openpyxl，已跳过 Excel 导出"])
        result.warnings = dedupe_warnings(self.warnings)
        result.exports = export_mod.collect_exports(
            self.job_dir,
            self.job.id,
            has_srt=bool(transcript),
            has_xlsx=xlsx_path is not None,
        )
        (self.job_dir / "result.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        return result


def run_job(job_id: str, settings: Settings = default_settings) -> None:
    """后台任务入口：根据 job_id 加载并执行流水线。"""

    job = get_job(job_id)
    if job is None:
        return
    runner = JobRunner(job, settings)
    try:
        runner.run()
    except Exception:  # noqa: BLE001 - 已在 runner 内记录失败状态
        pass


def load_result(job_id: str, settings: Settings = default_settings) -> Optional[JobResult]:
    path = settings.job_dir(job_id) / "result.json"
    if not path.exists():
        return None
    return JobResult.model_validate_json(path.read_text(encoding="utf-8"))


def save_transcript(
    job_id: str,
    segments: List[TranscriptSegment],
    settings: Settings = default_settings,
) -> None:
    """覆盖保存用户编辑后的逐字稿，并同步重写 SRT。"""

    job_dir = settings.job_dir(job_id)
    asr_mod.write_transcript(segments, job_dir)
    asr_mod.write_srt(segments, job_dir)


def reanalyze_job(job_id: str, settings: Settings = default_settings) -> None:
    """基于已存逐字稿与画面信息，仅重跑分析与导出，不重新下载或转写。"""

    job = get_job(job_id)
    if job is None:
        return
    job_dir = settings.job_dir(job_id)
    runner = JobRunner(job, settings)
    runner.metadata = _load_metadata(job_dir, job)

    transcript = _load_transcript(job_dir)
    frames = _load_frames(job_dir)
    runner.scene_changes = _load_scenes(job_dir)
    try:
        analysis, clips = runner._stage_report(transcript, frames)
        runner._stage_export(transcript, frames, analysis, clips)
        runner._set_status(JobStatus.COMPLETED)
    except Exception as exc:  # noqa: BLE001
        runner._fail(f"重新分析失败：{exc}")


def retry_job(job_id: str, settings: Settings = default_settings) -> None:
    """重试失败任务：重置状态并重新执行完整流水线。"""

    job = get_job(job_id)
    if job is None:
        return
    job.status = JobStatus.PENDING
    job.progress = 0
    job.error = None
    job.warnings = []
    save_job(job)
    run_job(job_id, settings)


def _load_metadata(job_dir: Path, job: Job) -> VideoMetadata:
    result_path = job_dir / "result.json"
    if result_path.exists():
        try:
            stored = JobResult.model_validate_json(
                result_path.read_text(encoding="utf-8")
            )
            return stored.metadata
        except Exception:  # noqa: BLE001
            pass
    meta_path = job_dir / "metadata.json"
    if meta_path.exists():
        try:
            return VideoMetadata.model_validate_json(
                meta_path.read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001
            pass
    return VideoMetadata(url=job.input_url)


def _load_transcript(job_dir: Path) -> List[TranscriptSegment]:
    import json

    path = job_dir / "transcript.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [TranscriptSegment(**item) for item in data]
    except Exception:  # noqa: BLE001
        return []


def _load_frames(job_dir: Path) -> List[FrameAnalysis]:
    import json

    path = job_dir / "frames.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [FrameAnalysis(**item) for item in data]
    except Exception:  # noqa: BLE001
        return []


def _load_scenes(job_dir: Path) -> List[float]:
    import json

    path = job_dir / "scenes.json"
    if not path.exists():
        return []
    try:
        return [float(x) for x in json.loads(path.read_text(encoding="utf-8"))]
    except Exception:  # noqa: BLE001
        return []


def _find_uploaded(job_dir: Path) -> Optional[Path]:
    for p in sorted(job_dir.glob("video.*")):
        if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".flv", ".m4v"}:
            return p
    return None
