from celery import Celery, shared_task
from models import db, Article, ArticleStatus
import datetime
from flask import Flask


def make_celery(app):
    celery = Celery(app.import_name, broker=app.config['CELERY_BROKER_URL'])
    celery.conf.update(app.config)
    return celery

@shared_task
def check_for_scheduled_articles():
    from app import app
    with app.app_context():
        now = datetime.datetime.utcnow()
        to_publish = Article.query.filter(
            Article.status == ArticleStatus.SCHEDULED,
            Article.scheduled_at <= now
        ).all()

        for article in to_publish:
            article.status = ArticleStatus.PUBLISHED

        db.session.commit()
        return f"Opublikowano {len(to_publish)} artykułów."