from __future__ import annotations

from app.config import load_settings
from app.models import ClipSuggestion, FrameAnalysis, TranscriptSegment
from app.pipeline.analyze import _heuristic_clips
from app.pipeline.ingest import normalize_url
from app.pipeline.utils import format_clock, format_srt_time
from app.pipeline.vision import select_cover_candidates


def test_normalize_url_from_share_text():
    text = "看看这个视频 https://v.douyin.com/abc123/ 复制打开抖音"
    assert normalize_url(text) == "https://v.douyin.com/abc123/"


def test_normalize_url_none():
    assert normalize_url("没有链接的文案") is None


def test_format_srt_time():
    assert format_srt_time(0) == "00:00:00,000"
    assert format_srt_time(3.2) == "00:00:03,200"
    assert format_srt_time(3661.5) == "01:01:01,500"


def test_format_clock():
    assert format_clock(0) == "00:00"
    assert format_clock(75) == "01:15"


def test_heuristic_clips_respects_min_duration():
    settings = load_settings()
    transcript = [
        TranscriptSegment(start=float(i * 6), end=float(i * 6 + 6), text=f"第{i}句话内容")
        for i in range(20)
    ]
    clips = _heuristic_clips(transcript, settings)
    assert clips, "应至少切出一条片段"
    for clip in clips:
        assert clip.end > clip.start
        assert clip.platforms


def test_select_cover_candidates_assigns_nearest_frame():
    frames = [
        FrameAnalysis(time=2.5, frame="frame_0001.jpg"),
        FrameAnalysis(time=7.5, frame="frame_0002.jpg"),
        FrameAnalysis(time=12.5, frame="frame_0003.jpg"),
    ]
    clips = [
        ClipSuggestion(index=1, start=0.0, end=8.0),
        ClipSuggestion(index=2, start=12.0, end=20.0),
    ]
    candidates = select_cover_candidates(frames, clips, "job_test")
    assert clips[0].cover_frame == "/downloads/job_test/frames/frame_0001.jpg"
    assert clips[1].cover_frame == "/downloads/job_test/frames/frame_0003.jpg"
    assert candidates == [
        "/downloads/job_test/frames/frame_0001.jpg",
        "/downloads/job_test/frames/frame_0003.jpg",
    ]


def test_select_cover_candidates_empty_frames():
    assert select_cover_candidates([], [ClipSuggestion(index=1, start=0, end=5)], "j") == []
