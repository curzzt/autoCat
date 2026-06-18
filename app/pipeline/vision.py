from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from ..config import Settings
from ..models import FrameAnalysis


def analyze_frames(
    frames: List[Path], settings: Settings
) -> Tuple[List[FrameAnalysis], List[str]]:
    """对关键帧做 OCR 与基础画面描述。

    OCR 不可用时降级：仍返回带时间点的帧列表，但 ocr_text 为空。
    任何失败都不阻断主流程，只记 warning。
    """

    warnings: List[str] = []
    interval = max(1, settings.media.keyframe_interval_seconds)

    ocr_engine = _load_ocr()
    if ocr_engine is None:
        warnings.append("未安装 RapidOCR，已跳过画面文字识别，基于逐字稿继续分析")

    results: List[FrameAnalysis] = []
    for i, frame in enumerate(frames):
        time_point = round(i * interval + interval / 2, 1)
        ocr_text = ""
        if ocr_engine is not None:
            try:
                ocr_text = _run_ocr(ocr_engine, frame)
            except Exception as exc:  # noqa: BLE001 - OCR 不应中断主流程
                warnings.append(f"第 {i + 1} 帧 OCR 失败：{exc}")
        results.append(
            FrameAnalysis(
                time=time_point,
                frame=str(frame.name),
                ocr_text=ocr_text or None,
                description=_describe(ocr_text),
            )
        )
    return results, warnings


def _describe(ocr_text: str) -> str:
    if ocr_text:
        return "画面包含文字信息，可能为口播标题或字幕。"
    return "未检测到明显文字，可能为口播或实拍画面。"


def _load_ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except ImportError:
        return None
    try:
        return RapidOCR()
    except Exception:  # noqa: BLE001
        return None


def _run_ocr(engine, frame: Path) -> str:
    result, _elapse = engine(str(frame))
    texts: List[str] = []
    for item in result or []:
        if len(item) >= 2 and isinstance(item[1], str):
            texts.append(item[1])
    return " ".join(texts).strip()


def _frame_url(job_id: str, frame_name: str) -> str:
    return f"/downloads/{job_id}/frames/{frame_name}"


def select_cover_candidates(
    frames: List[FrameAnalysis],
    clips,
    job_id: str,
) -> List[str]:
    """为每条切片选取最接近开始时间的关键帧作为封面候选，并汇总候选列表。

    无关键帧时返回空列表，不影响其它字段。
    """

    if not frames:
        return []

    candidates: List[str] = []
    for clip in clips:
        nearest = min(frames, key=lambda f: abs(f.time - clip.start))
        url = _frame_url(job_id, nearest.frame)
        clip.cover_frame = url
        if url not in candidates:
            candidates.append(url)

    # 没有切片时退化为均匀采样几张候选帧
    if not candidates:
        step = max(1, len(frames) // 4)
        for f in frames[::step][:4]:
            candidates.append(_frame_url(job_id, f.frame))
    return candidates


def write_frames(frames: List[FrameAnalysis], job_dir: Path) -> Path:
    path = job_dir / "frames.json"
    path.write_text(
        json.dumps([f.model_dump() for f in frames], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
