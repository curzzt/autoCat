from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import Settings
from ..models import VideoMetadata
from .utils import PipelineError, run_command

_URL_RE = re.compile(r"https?://[^\s,，]+")


def normalize_url(text: str) -> Optional[str]:
    """从抖音分享文案中提取并标准化 URL。"""

    if not text:
        return None
    match = _URL_RE.search(text)
    if match:
        return match.group(0).strip()
    text = text.strip()
    if text.startswith("www."):
        return "https://" + text
    return None


def _metadata_from_ytdlp(info: dict, url: str) -> VideoMetadata:
    return VideoMetadata(
        url=url,
        title=info.get("title"),
        author=info.get("uploader") or info.get("creator") or info.get("channel"),
        duration=info.get("duration"),
        likes=info.get("like_count"),
        comments=info.get("comment_count"),
        shares=info.get("repost_count"),
        favorites=info.get("favorite_count"),
    )


def download_video(
    url: str,
    job_dir: Path,
    settings: Settings,
) -> Tuple[Optional[Path], VideoMetadata, List[str]]:
    """通过 yt-dlp 适配层下载视频并提取元数据。

    失败不直接抛错给上层中断，而是返回 warnings，由编排层决定是否兜底。
    但完全无视频可用时抛 PipelineError。
    """

    warnings: List[str] = []
    (job_dir / "input.json").write_text(
        json.dumps({"url": url}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not settings.ytdlp_path:
        raise PipelineError("未检测到 yt-dlp，无法自动下载视频。请安装 yt-dlp 或改用本地上传。")

    output_template = str(job_dir / "video.%(ext)s")
    info_path = job_dir / "ytdlp_info.json"

    args = [
        settings.ytdlp_path,
        "--no-playlist",
        "--no-warnings",
        "--write-info-json",
        "--print-json",
        "-o",
        output_template,
        url,
    ]

    metadata = VideoMetadata(url=url)
    try:
        proc = run_command(args, timeout=600, check=True)
        stdout = proc.stdout.decode("utf-8", "ignore").strip()
        if stdout:
            last_line = stdout.splitlines()[-1]
            try:
                info = json.loads(last_line)
                metadata = _metadata_from_ytdlp(info, url)
            except json.JSONDecodeError:
                warnings.append("yt-dlp 未返回可解析的元数据")
    except PipelineError as exc:
        raise PipelineError(f"视频下载失败：{exc}") from exc

    video_path = _locate_video(job_dir)
    if video_path is None:
        # 兜底：从 info json 里读取元数据，但标记下载失败
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                metadata = _metadata_from_ytdlp(info, url)
            except (OSError, json.JSONDecodeError):
                pass
        raise PipelineError("下载完成但未找到视频文件，可能受平台反爬影响。")

    # 标准化为 video.mp4 引用名（保留原扩展名文件）
    metadata_path = job_dir / "metadata.json"
    metadata_path.write_text(
        metadata.model_dump_json(indent=2), encoding="utf-8"
    )
    return video_path, metadata, warnings


def register_uploaded_video(
    src_path: Path,
    job_dir: Path,
    settings: Settings,
) -> Tuple[Path, VideoMetadata]:
    """处理本地上传的视频，作为下载失败的兜底入口。"""

    suffix = src_path.suffix or ".mp4"
    target = job_dir / f"video{suffix}"
    if src_path.resolve() != target.resolve():
        shutil.copyfile(src_path, target)
    metadata = VideoMetadata(url=None, title=src_path.stem)
    (job_dir / "metadata.json").write_text(
        metadata.model_dump_json(indent=2), encoding="utf-8"
    )
    return target, metadata


def _locate_video(job_dir: Path) -> Optional[Path]:
    candidates = sorted(
        p
        for p in job_dir.glob("video.*")
        if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".flv", ".m4v"}
    )
    return candidates[0] if candidates else None
