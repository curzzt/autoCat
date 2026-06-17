from __future__ import annotations

import subprocess
from typing import List, Optional, Sequence


class PipelineError(RuntimeError):
    """流水线阶段的可恢复错误。携带 message 供结果页展示。"""


def run_command(
    args: Sequence[str],
    timeout: Optional[int] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """执行外部命令并返回结果。失败时抛出 PipelineError。"""

    try:
        proc = subprocess.run(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise PipelineError(f"命令不存在: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"命令超时: {' '.join(map(str, args))}") from exc

    if check and proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "ignore").strip()
        raise PipelineError(
            f"命令失败({proc.returncode}): {' '.join(map(str, args))}\n{stderr[:500]}"
        )
    return proc


def format_srt_time(seconds: float) -> str:
    """将秒转换为 SRT 时间码 HH:MM:SS,mmm。"""

    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3600 * 1000)
    minutes, millis = divmod(millis, 60 * 1000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_clock(seconds: float) -> str:
    """将秒转换为 MM:SS 展示用时间。"""

    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def dedupe_warnings(warnings: List[str]) -> List[str]:
    seen = set()
    result = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result
