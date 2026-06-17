from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from ..config import Settings
from ..models import TranscriptSegment
from .utils import PipelineError, format_srt_time


def transcribe(
    audio_path: Path, settings: Settings
) -> Tuple[List[TranscriptSegment], List[str]]:
    """将音频转写为带时间轴的逐字稿。

    优先使用 faster-whisper；不可用时抛 PipelineError 由编排层处理。
    """

    provider = settings.asr.provider.lower()
    if provider in {"faster-whisper", "whisper"}:
        return _transcribe_faster_whisper(audio_path, settings)
    raise PipelineError(f"未知 ASR 提供方: {settings.asr.provider}")


def _transcribe_faster_whisper(
    audio_path: Path, settings: Settings
) -> Tuple[List[TranscriptSegment], List[str]]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise PipelineError(
            "未安装 faster-whisper，无法转写。请执行 pip install faster-whisper。"
        ) from exc

    warnings: List[str] = []
    model = WhisperModel(
        settings.asr.model,
        device=settings.asr.device,
        compute_type=settings.asr.compute_type,
    )
    segments_iter, _info = model.transcribe(
        str(audio_path),
        language="zh",
        vad_filter=True,
    )

    segments: List[TranscriptSegment] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                start=round(float(seg.start), 2),
                end=round(float(seg.end), 2),
                text=text,
            )
        )
    if not segments:
        warnings.append("ASR 未识别到有效语音内容")
    return segments, warnings


def write_transcript(segments: List[TranscriptSegment], job_dir: Path) -> Path:
    path = job_dir / "transcript.json"
    path.write_text(
        json.dumps(
            [s.model_dump() for s in segments], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return path


def write_srt(segments: List[TranscriptSegment], job_dir: Path) -> Path:
    path = job_dir / "subtitle.srt"
    lines: List[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}")
        lines.append(seg.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
