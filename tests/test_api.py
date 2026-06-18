from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from app.config import settings
from app.db import create_job, get_job
from app.main import app
from app.models import Job, JobStatus

client = TestClient(app)


def _make_job(job_id: str, status: JobStatus = JobStatus.FAILED) -> None:
    if get_job(job_id) is None:
        create_job(Job(id=job_id, source="upload", status=status))


def test_index_ok():
    assert client.get("/").status_code == 200


def test_history_page_ok():
    assert client.get("/jobs").status_code == 200


def test_status_missing_job():
    assert client.get("/api/jobs/not_exist").status_code == 404


def test_retry_missing_job():
    assert client.post("/api/jobs/not_exist/retry").status_code == 404


def test_retry_existing_job():
    jid = "job_api_retry_test"
    _make_job(jid, JobStatus.FAILED)
    try:
        resp = client.post(f"/api/jobs/{jid}/retry")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
    finally:
        shutil.rmtree(settings.job_dir(jid), ignore_errors=True)


def test_transcript_update_and_reanalyze():
    jid = "job_api_reanalyze_test"
    _make_job(jid, JobStatus.COMPLETED)
    try:
        put = client.put(
            f"/api/jobs/{jid}/transcript",
            json={"segments": [{"start": 0, "end": 3, "text": "测试文本"}]},
        )
        assert put.status_code == 200
        assert put.json()["segments"] == 1
        re = client.post(f"/api/jobs/{jid}/reanalyze")
        assert re.status_code == 200
    finally:
        shutil.rmtree(settings.job_dir(jid), ignore_errors=True)


def test_frame_download_missing():
    jid = "job_api_frame_test"
    _make_job(jid, JobStatus.COMPLETED)
    try:
        assert client.get(f"/downloads/{jid}/frames/missing.jpg").status_code == 404
    finally:
        shutil.rmtree(settings.job_dir(jid), ignore_errors=True)
