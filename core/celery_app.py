import os

from celery import Celery
from celery.schedules import crontab
from kombu import Queue


def _task_modules() -> list[str]:
    raw = os.environ.get("CELERY_INCLUDE", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


app = Celery(
    "spy_pipeline",
    broker=os.environ.get("REDIS_URL"),
    include=_task_modules(),
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_routes={
        "tasks.qualification.*": {"queue": "qualification_queue"},
        "src.worker_ia.processar_anuncio_multimodal": {"queue": "ai_multimodal_queue"},
        "src.worker_ia.reprocessar_falha_definitiva": {"queue": "ai_dead_letter"},
        "tasks.lifecycle.expirar_ofertas_fracas": {"queue": "lifecycle_queue"},
    },
    beat_schedule={
        "expirar-ofertas-fracas": {
            "task": "tasks.lifecycle.expirar_ofertas_fracas",
            "schedule": crontab(hour=3, minute=0),
        },
    },
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_ignore_result=True,
    task_store_errors_even_if_ignored=True,
    task_queues=(
        Queue("qualification_queue"),
        Queue("ai_multimodal_queue"),
        Queue("ai_dead_letter"),
        Queue("lifecycle_queue"),
    ),
)
