from __future__ import annotations

import argparse
import sys
from datetime import datetime

from ..config import settings
from ..db import create_job, init_db
from ..jobs import JobRunner, load_result
from ..models import Job, JobStatus
from .ingest import normalize_url


def _new_job_id() -> str:
    return "job_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="autocat",
        description="抖音自动拆片命令行：输入链接生成逐字稿、爆款分析与拆片报告。",
    )
    parser.add_argument("url", help="抖音视频链接或分享文案")
    args = parser.parse_args(argv)

    url = normalize_url(args.url) or args.url.strip()
    if not url:
        print("无法解析链接", file=sys.stderr)
        return 2

    init_db()
    job = Job(id=_new_job_id(), input_url=url, source="url", status=JobStatus.PENDING)
    create_job(job)

    print(f"任务已创建：{job.id}")
    runner = JobRunner(job, settings)
    try:
        runner.run()
    except Exception as exc:  # noqa: BLE001
        print(f"任务失败：{exc}", file=sys.stderr)
        return 1

    result = load_result(job.id, settings)
    job_dir = settings.job_dir(job.id)
    print(f"完成。产物目录：{job_dir}")
    print(f"Markdown 报告：{job_dir / 'report.md'}")
    if result and result.exports.get("xlsx"):
        print(f"Excel 拆片表：{job_dir / 'clips.xlsx'}")
    print(f"SRT 字幕：{job_dir / 'subtitle.srt'}")
    if result and result.warnings:
        print("告警：")
        for w in result.warnings:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
