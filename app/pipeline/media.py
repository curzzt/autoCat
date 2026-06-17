from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import Settings
from .utils import PipelineError, run_command


def probe_media(video_path: Path, settings: Settings) -> dict:
    """使用 ffprobe 获取媒体信息。无 ffprobe 时返回空信息。"""

    if not settings.ffprobe_path:
        return {}
    proc = run_command(
        [
            settings.ffprobe_path,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ],
        timeout=60,
    )
    try:
        info = json.loads(proc.stdout.decode("utf-8", "ignore") or "{}")
    except json.JSONDecodeError:
        info = {}
    (video_path.parent / "media_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return info


def duration_from_probe(info: dict) -> Optional[float]:
    fmt = info.get("format") or {}
    raw = fmt.get("duration")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    for stream in info.get("streams", []):
        raw = stream.get("duration")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
    return None


def extract_audio(video_path: Path, settings: Settings) -> Path:
    """抽取 16k 单声道 wav 音频。"""

    if not settings.ffmpeg_path:
        raise PipelineError("未检测到 ffmpeg，无法抽取音频。")
    audio_path = video_path.parent / "audio.wav"
    run_command(
        [
            settings.ffmpeg_path,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(settings.media.audio_sample_rate),
            str(audio_path),
        ],
        timeout=600,
    )
    return audio_path


def extract_keyframes(
    video_path: Path, settings: Settings
) -> Tuple[List[Path], List[str]]:
    """按固定间隔抽取关键帧。失败时降级返回空列表与 warning。"""

    warnings: List[str] = []
    if not settings.ffmpeg_path:
        return [], ["未检测到 ffmpeg，跳过关键帧抽取"]

    frames_dir = video_path.parent / "frames"
    frames_dir.mkdir(exist_ok=True)
    interval = max(1, settings.media.keyframe_interval_seconds)

    try:
        run_command(
            [
                settings.ffmpeg_path,
                "-y",
                "-i",
                str(video_path),
                "-vf",
                f"fps=1/{interval}",
                str(frames_dir / "frame_%04d.jpg"),
            ],
            timeout=600,
        )
    except PipelineError as exc:
        warnings.append(f"关键帧抽取失败：{exc}")
        return [], warnings

    frames = sorted(frames_dir.glob("frame_*.jpg"))
    return frames, warnings
