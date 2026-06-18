from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

from ..config import Settings
from ..models import Analysis, ClipSuggestion, FrameAnalysis, TranscriptSegment, VideoMetadata
from .utils import dedupe_warnings


def analyze(
    metadata: VideoMetadata,
    transcript: List[TranscriptSegment],
    frames: List[FrameAnalysis],
    settings: Settings,
    scene_changes: Optional[List[float]] = None,
) -> Tuple[Analysis, List[ClipSuggestion], List[str]]:
    """LLM 爆款分析 + 拆片。LLM 不可用或失败时降级为启发式拆片。

    scene_changes 为镜头切换时间点，用于把启发式切片边界对齐到画面变化处。
    """

    warnings: List[str] = []

    if not transcript:
        warnings.append("无逐字稿可分析，拆片结果可能不可靠")

    if settings.llm.enabled and transcript:
        try:
            analysis = _llm_hotspot_analysis(metadata, transcript, frames, settings)
            clips = _llm_clip_suggestions(metadata, transcript, settings)
            if clips:
                return analysis, clips, warnings
            warnings.append("LLM 未返回有效拆片，已降级为启发式拆片")
            return analysis, _heuristic_clips(transcript, settings, scene_changes), warnings
        except Exception as exc:  # noqa: BLE001 - LLM 失败降级
            warnings.append(f"LLM 分析失败，已降级为启发式分析：{exc}")

    analysis = _heuristic_analysis(transcript)
    clips = _heuristic_clips(transcript, settings, scene_changes)
    return analysis, clips, dedupe_warnings(warnings)


# --------------------------- LLM 路径 ---------------------------


def _build_context(
    metadata: VideoMetadata,
    transcript: List[TranscriptSegment],
    frames: List[FrameAnalysis],
) -> str:
    lines: List[str] = []
    lines.append("【视频基础信息】")
    lines.append(json.dumps(metadata.model_dump(), ensure_ascii=False))
    lines.append("【带时间轴逐字稿】")
    for seg in transcript:
        lines.append(f"[{seg.start:.1f}-{seg.end:.1f}] {seg.text}")
    if frames:
        lines.append("【关键帧画面信息】")
        for f in frames:
            if f.ocr_text:
                lines.append(f"[{f.time:.1f}s] {f.ocr_text}")
    return "\n".join(lines)


def _chat(settings: Settings, system: str, user: str) -> str:
    url = settings.llm.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm.api_key}"}
    payload = {
        "model": settings.llm.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=settings.llm.timeout_seconds) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


def _parse_json(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1:
        content = content[start : end + 1]
    return json.loads(content)


def _llm_hotspot_analysis(
    metadata: VideoMetadata,
    transcript: List[TranscriptSegment],
    frames: List[FrameAnalysis],
    settings: Settings,
) -> Analysis:
    system = (
        "你是资深短视频内容分析师。请基于给定的视频信息、逐字稿和画面信息，"
        "拆解这条视频的爆款逻辑。只输出 JSON，字段："
        "hook(开头3秒钩子), conflict(核心冲突或悬念), emotion_value(情绪价值), "
        "info_value(信息价值), trust(信任背书), share_reason(转发理由), "
        "comment_trigger(评论区触发点), structure(内容结构拆解,字符串数组), "
        "risks(风险点,字符串数组), summary(一句话总结)。"
    )
    context = _build_context(metadata, transcript, frames)
    raw = _chat(settings, system, context)
    data = _parse_json(raw)
    return Analysis(
        hook=data.get("hook"),
        conflict=data.get("conflict"),
        emotion_value=data.get("emotion_value"),
        info_value=data.get("info_value"),
        trust=data.get("trust"),
        share_reason=data.get("share_reason"),
        comment_trigger=data.get("comment_trigger"),
        structure=_as_str_list(data.get("structure")),
        risks=_as_str_list(data.get("risks")),
        summary=data.get("summary"),
    )


def _llm_clip_suggestions(
    metadata: VideoMetadata,
    transcript: List[TranscriptSegment],
    settings: Settings,
) -> List[ClipSuggestion]:
    platforms = "、".join(settings.clip.target_platforms)
    system = (
        "你是短视频拆片专家。请把这条视频拆成可独立成立的切片。约束："
        f"每条 {settings.clip.min_seconds}-{settings.clip.max_seconds} 秒；"
        "优先强钩子、强情绪、强反差、强干货、强争议、强结果展示片段；"
        "每条必须能独立成立；必须给出标题、封面文案、开头字幕、剪辑建议。"
        f"目标平台从 [{platforms}] 中选择。只输出 JSON，结构为 "
        '{"clips":[{"start":数字,"end":数字,"title":"","hotspot_type":"",'
        '"reason":"","opening_subtitle":"","cover_text":"",'
        '"edit_suggestion":"","platforms":[],"potential":"高/中/低"}]}。'
    )
    user = "【逐字稿】\n" + "\n".join(
        f"[{seg.start:.1f}-{seg.end:.1f}] {seg.text}" for seg in transcript
    )
    raw = _chat(settings, system, user)
    data = _parse_json(raw)
    clips: List[ClipSuggestion] = []
    for i, item in enumerate(data.get("clips", []), start=1):
        try:
            clips.append(
                ClipSuggestion(
                    index=i,
                    start=float(item.get("start", 0)),
                    end=float(item.get("end", 0)),
                    title=str(item.get("title", "")),
                    hook=str(item.get("hook", item.get("opening_subtitle", ""))),
                    opening_subtitle=str(item.get("opening_subtitle", "")),
                    cover_text=str(item.get("cover_text", "")),
                    hotspot_type=str(item.get("hotspot_type", "")),
                    reason=str(item.get("reason", "")),
                    edit_suggestion=str(item.get("edit_suggestion", "")),
                    platforms=_as_str_list(item.get("platforms")),
                    potential=str(item.get("potential", "")),
                )
            )
        except (TypeError, ValueError):
            continue
    return clips


# --------------------------- 启发式降级路径 ---------------------------


def _heuristic_analysis(transcript: List[TranscriptSegment]) -> Analysis:
    opening = transcript[0].text if transcript else ""
    full_text = "".join(seg.text for seg in transcript)
    return Analysis(
        hook=opening or None,
        conflict=None,
        emotion_value=None,
        info_value=None,
        trust=None,
        share_reason=None,
        comment_trigger=None,
        structure=[
            "开头：" + (opening[:40] if opening else "（无逐字稿）"),
            f"主体：共 {len(transcript)} 段口播",
            "结尾：" + (transcript[-1].text[:40] if transcript else "（无逐字稿）"),
        ],
        risks=["未配置 LLM，本分析为启发式生成，仅供参考"],
        summary=(full_text[:60] + "…") if len(full_text) > 60 else (full_text or None),
    )


def _heuristic_clips(
    transcript: List[TranscriptSegment],
    settings: Settings,
    scene_changes: Optional[List[float]] = None,
) -> List[ClipSuggestion]:
    """按目标时长窗口把逐字稿切成多条候选片段。

    若提供 scene_changes，则在满足最小时长后优先在镜头切换处断开，使切片边界更自然。
    """

    if not transcript:
        return []

    scenes = scene_changes or []
    min_s = settings.clip.min_seconds
    max_s = settings.clip.max_seconds
    target = (min_s + max_s) / 2

    clips: List[ClipSuggestion] = []
    bucket: List[TranscriptSegment] = []
    bucket_start: Optional[float] = None

    def flush(end_time: float) -> None:
        if not bucket or bucket_start is None:
            return
        duration = end_time - bucket_start
        if duration < min_s and clips:
            # 太短则并入上一条
            prev = clips[-1]
            prev.end = end_time
            prev.title = prev.title
            return
        text = "".join(s.text for s in bucket)
        idx = len(clips) + 1
        clips.append(
            ClipSuggestion(
                index=idx,
                start=round(bucket_start, 1),
                end=round(end_time, 1),
                title=text[:18] if text else f"切片 {idx}",
                hook=bucket[0].text[:30],
                opening_subtitle=bucket[0].text[:20],
                cover_text=text[:12],
                hotspot_type="待人工判定",
                reason="基于逐字稿时长窗口自动切分，建议人工复核爆点。",
                edit_suggestion="前 2 秒放大字幕，保留停顿，结尾加总结卡片。",
                platforms=list(settings.clip.target_platforms),
                potential="中",
            )
        )

    for seg in transcript:
        if bucket_start is None:
            bucket_start = seg.start
        bucket.append(seg)
        duration = seg.end - bucket_start
        scene_break = duration >= min_s and any(
            bucket_start + min_s <= sc <= seg.end for sc in scenes
        )
        if duration >= target or scene_break:
            flush(seg.end)
            bucket = []
            bucket_start = None

    if bucket and bucket_start is not None:
        flush(bucket[-1].end)

    return clips


def _as_str_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]
