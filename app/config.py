from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _resolve_tool(env_name: str, tool_name: str) -> Optional[str]:
    explicit = _env(env_name)
    if explicit:
        return explicit
    found = shutil.which(tool_name)
    if found:
        return found
    if tool_name == "ffmpeg":
        return _imageio_ffmpeg()
    return None


def _imageio_ffmpeg() -> Optional[str]:
    """无系统 ffmpeg 时，回退到 imageio-ffmpeg 自带的二进制。"""

    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


@dataclass
class ClipConfig:
    min_seconds: int = 15
    max_seconds: int = 45
    target_platforms: List[str] = field(
        default_factory=lambda: ["douyin", "xiaohongshu", "shipinhao"]
    )


@dataclass
class MediaConfig:
    audio_sample_rate: int = 16000
    keyframe_interval_seconds: int = 5


@dataclass
class AnalysisConfig:
    language: str = "zh-CN"
    output_format: str = "json"
    continue_on_partial_failure: bool = True


@dataclass
class LLMConfig:
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 120

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)


@dataclass
class ASRConfig:
    provider: str = "faster-whisper"
    model: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"


@dataclass
class Settings:
    base_dir: Path
    storage_dir: Path
    jobs_dir: Path
    database_path: Path

    ffmpeg_path: Optional[str]
    ffprobe_path: Optional[str]
    ytdlp_path: Optional[str]

    clip: ClipConfig = field(default_factory=ClipConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        path = self.jobs_dir / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path


def load_settings() -> Settings:
    base_dir = Path(_env("AUTOCAT_BASE_DIR", str(Path(__file__).resolve().parent.parent)))
    storage_dir = Path(_env("AUTOCAT_STORAGE_DIR", str(base_dir / "storage")))
    jobs_dir = storage_dir / "jobs"
    database_path = Path(_env("AUTOCAT_DB_PATH", str(storage_dir / "autocat.db")))

    settings = Settings(
        base_dir=base_dir,
        storage_dir=storage_dir,
        jobs_dir=jobs_dir,
        database_path=database_path,
        ffmpeg_path=_resolve_tool("AUTOCAT_FFMPEG", "ffmpeg"),
        ffprobe_path=_resolve_tool("AUTOCAT_FFPROBE", "ffprobe"),
        ytdlp_path=_resolve_tool("AUTOCAT_YTDLP", "yt-dlp"),
        clip=ClipConfig(
            min_seconds=_env_int("AUTOCAT_CLIP_MIN", 15),
            max_seconds=_env_int("AUTOCAT_CLIP_MAX", 45),
        ),
        media=MediaConfig(
            audio_sample_rate=_env_int("AUTOCAT_AUDIO_SR", 16000),
            keyframe_interval_seconds=_env_int("AUTOCAT_KEYFRAME_INTERVAL", 5),
        ),
        llm=LLMConfig(
            base_url=_env("AUTOCAT_LLM_BASE_URL"),
            api_key=_env("AUTOCAT_LLM_API_KEY"),
            model=_env("AUTOCAT_LLM_MODEL", "gpt-4o-mini"),
            timeout_seconds=_env_int("AUTOCAT_LLM_TIMEOUT", 120),
        ),
        asr=ASRConfig(
            provider=_env("AUTOCAT_ASR_PROVIDER", "faster-whisper"),
            model=_env("AUTOCAT_ASR_MODEL", "small"),
            device=_env("AUTOCAT_ASR_DEVICE", "cpu"),
            compute_type=_env("AUTOCAT_ASR_COMPUTE", "int8"),
        ),
    )
    settings.ensure_dirs()
    return settings


settings = load_settings()


def apply_llm_config(values: dict) -> None:
    """就地更新全局 settings.llm，使所有持有该单例引用的模块即时生效。

    仅更新 values 中出现的键；base_url/api_key 传空字符串表示清空。
    """

    llm = settings.llm
    if "base_url" in values:
        llm.base_url = (values["base_url"] or "").strip() or None
    if "api_key" in values:
        llm.api_key = (values["api_key"] or "").strip() or None
    if values.get("model"):
        llm.model = str(values["model"]).strip()
    if values.get("timeout_seconds") is not None:
        try:
            llm.timeout_seconds = int(values["timeout_seconds"])
        except (TypeError, ValueError):
            pass
