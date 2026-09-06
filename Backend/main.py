import os
import re

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask.typing import ResponseReturnValue
from flask_cors import CORS
from sqlalchemy import and_, case, select

from db import SessionLocal, init_db
from gpt import list_ollama_models
from logstream import log
from models import Topic
from repository import (
    add_topic,
    as_utc,
    create_job,
    get_job,
    list_artifacts,
    list_job_events,
    list_topics,
    normalize_subject,
    request_cancel,
)
from utils import ENV_FILE, SONGS_DIR, check_env_vars, clean_dir


load_dotenv(ENV_FILE)
check_env_vars()
init_db()

app = Flask(__name__)
CORS(app)

HOST = "0.0.0.0"
PORT = 8080


@app.route("/api/models", methods=["GET"])
def models():
    try:
        available_models, default_model = list_ollama_models()
        return jsonify(
            {
                "status": "success",
                "models": available_models,
                "default": default_model,
            }
        )
    except Exception as err:
        log(f"[-] Error fetching Ollama models: {str(err)}", "error")
        return jsonify(
            {
                "status": "error",
                "message": "Could not fetch Ollama models. Is Ollama running?",
                "models": [os.getenv("OLLAMA_MODEL", "llama3.1:8b")],
                "default": os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            }
        )


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json() or {}
    if not data.get("videoSubject"):
        return jsonify({"status": "error", "message": "videoSubject is required."}), 400

    with SessionLocal() as session:
        job = create_job(session, payload=data)

    return jsonify(
        {
            "status": "success",
            "message": "Video generation queued.",
            "jobId": job.id,
        }
    )


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job_status(job_id: str):
    with SessionLocal() as session:
        job = get_job(session, job_id)
        if not job:
            return jsonify({"status": "error", "message": "Job not found."}), 404

        return jsonify(
            {
                "status": "success",
                "job": {
                    "id": job.id,
                    "state": job.status,
                    "cancelRequested": job.cancel_requested,
                    "resultPath": job.result_path,
                    "errorMessage": job.error_message,
                    "createdAt": job.created_at.isoformat() if job.created_at else None,
                    "startedAt": job.started_at.isoformat() if job.started_at else None,
                    "completedAt": job.completed_at.isoformat()
                    if job.completed_at
                    else None,
                    "artifacts": [
                        {
                            "type": artifact.artifact_type,
                            "path": artifact.path,
                            "metadata": artifact.metadata_json,
                            "createdAt": artifact.created_at.isoformat()
                            if artifact.created_at
                            else None,
                        }
                        for artifact in list_artifacts(session, job_id)
                    ],
                },
            }
        )


@app.route("/api/jobs/<job_id>/events", methods=["GET"])
def get_events(job_id: str):
    after_id = request.args.get("after", default=0, type=int)

    with SessionLocal() as session:
        job = get_job(session, job_id)
        if not job:
            return jsonify({"status": "error", "message": "Job not found."}), 404

        events = list_job_events(session, job_id, after_id=after_id)
        return jsonify(
            {
                "status": "success",
                "events": [
                    {
                        "id": event.id,
                        "type": event.event_type,
                        "level": event.level,
                        "message": event.message,
                        "payload": event.payload,
                        "timestamp": event.created_at.timestamp()
                        if event.created_at
                        else None,
                    }
                    for event in events
                ],
            }
        )


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    with SessionLocal() as session:
        cancelled = request_cancel(session, job_id)
        if not cancelled:
            return jsonify({"status": "error", "message": "Job not found."}), 404

    return jsonify({"status": "success", "message": "Cancellation requested."})


@app.route("/api/upload-songs", methods=["POST"])
def upload_songs():
    try:
        files = request.files.getlist("songs")
        if not files:
            return jsonify({"status": "error", "message": "No files uploaded."}), 400

        clean_dir(str(SONGS_DIR))
        saved = 0
        for file_item in files:
            if file_item.filename and file_item.filename.lower().endswith(".mp3"):
                safe_name = os.path.basename(file_item.filename)
                file_item.save(str(SONGS_DIR / safe_name))
                saved += 1

        if saved == 0:
            return jsonify({"status": "error", "message": "No MP3 files found."}), 400

        log(f"[+] Uploaded {saved} song(s) to {SONGS_DIR}", "success")
        return jsonify({"status": "success", "message": f"Uploaded {saved} song(s)."})
    except Exception as err:
        log(f"[-] Error uploading songs: {str(err)}", "error")
        return jsonify({"status": "error", "message": str(err)}), 500


@app.route("/api/cancel", methods=["POST"])
def cancel_latest_running_job():
    with SessionLocal() as session:
        from models import GenerationJob

        stmt = (
            select(GenerationJob)
            .where(and_(GenerationJob.status.in_(["queued", "running"])))
            .order_by(
                case((GenerationJob.status == "running", 0), else_=1),
                GenerationJob.created_at.desc(),
            )
            .limit(1)
        )
        latest_job = session.scalars(stmt).first()
        if not latest_job:
            return jsonify({"status": "error", "message": "No active job found."}), 404

        request_cancel(session, latest_job.id)

    return jsonify(
        {
            "status": "success",
            "message": "Cancellation requested.",
            "jobId": latest_job.id,
        }
    )


TOPIC_STATUSES = ("planned", "queued", "done", "failed")


TOPIC_SUBJECT_MAX_LENGTH = 255


def _topic_to_json(topic: Topic) -> dict:
    return {
        "id": topic.id,
        "subject": topic.subject,
        "niche": topic.niche,
        "source": topic.source,
        "status": topic.status,
        "jobId": topic.job_id,
        "createdAt": as_utc(topic.created_at).isoformat() if topic.created_at else None,
        "usedAt": as_utc(topic.used_at).isoformat() if topic.used_at else None,
        "completedAt": as_utc(topic.completed_at).isoformat() if topic.completed_at else None,
    }


@app.route("/api/topics", methods=["POST"])
def create_topic() -> ResponseReturnValue:
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    subject = data.get("subject")
    if not isinstance(subject, str) or not subject.strip() or not normalize_subject(subject):
        return jsonify({"status": "error", "message": "subject is required."}), 400
    cleaned = re.sub(r"\s+", " ", subject).strip()
    if len(cleaned) > TOPIC_SUBJECT_MAX_LENGTH:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"subject must be at most {TOPIC_SUBJECT_MAX_LENGTH} characters.",
                }
            ),
            400,
        )

    with SessionLocal() as session:
        topic = add_topic(session, subject, niche=None, source="manual")
        if topic is None:
            return jsonify({"status": "error", "message": "Topic already exists."}), 409
        body = _topic_to_json(topic)

    return jsonify({"status": "success", "topic": body}), 201


@app.route("/api/topics", methods=["GET"])
def get_topics() -> ResponseReturnValue:
    status = request.args.get("status")
    if status and status not in TOPIC_STATUSES:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"status must be one of {', '.join(TOPIC_STATUSES)}.",
                }
            ),
            400,
        )
    limit = request.args.get("limit", default=50, type=int)
    limit = max(1, min(limit, 500))

    with SessionLocal() as session:
        topics = [_topic_to_json(topic) for topic in list_topics(session, status, limit)]

    return jsonify({"status": "success", "topics": topics})


if __name__ == "__main__":
    app.run(debug=True, host=HOST, port=PORT, threaded=True)
