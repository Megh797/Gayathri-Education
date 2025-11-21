from celery import Celery
from dotenv import load_dotenv
import os

load_dotenv()

def make_celery():
    celery = Celery(
        "sms_app",
        broker="redis://localhost:6379/0",
        backend="redis://localhost:6379/1"
    )
    celery.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='Asia/Kolkata',
        enable_utc=True
    )
    return celery

celery = make_celery()
