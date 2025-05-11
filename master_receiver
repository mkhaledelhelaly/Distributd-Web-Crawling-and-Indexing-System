import redis
from celery import Celery
import json

# Celery app for receiving URLs
app = Celery('task_receiver', broker='redis://10.212.0.2:6379/0')

# Redis connection (same Redis as broker, but using it for data too)
r = redis.Redis(host='10.212.0.2', port=6379, db=0)

@app.task(name='enqueue_url')
def enqueue_url(url, depth):
    print(f"[Receiver] 📥 Received URL from client: {url}")
    print(f"The depth: {depth}")
    r.rpush('pending_urls', url)  # Push to the Redis list


@app.task(name="receive_index_data")
def receive_index_data(page_data):
    r.rpush("pending_index_data", json.dumps(page_data))
    print(f"[Receiver] 📨 Received data for indexing: {page_data['url']}")

