import os
import json
import psycopg2
from collections import Counter
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from google.cloud import storage
from whoosh.fields import Schema, TEXT, ID
from whoosh.index import create_in, open_dir, exists_in
from celery import Celery
import redis
import logging
import time


# ---------- Configuration ----------
REDIS_HOST = '10.212.0.2'
REDIS_PORT = 6379
DB_HOST = '10.212.0.9'
DB_NAME = 'searchengine'
DB_USER = 'crawleruser'
DB_PASSWORD = 'securepassword'
GCS_BUCKET_NAME = 'my-index-bucket'
BASE_INDEX_DIR = 'indexdir'

# ---------- Celery App ----------
app = Celery('indexer', broker=f'redis://{REDIS_HOST}:{REDIS_PORT}/0')

#------------Redis Connection-----------
r = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=0)

# ---------- Stop Words ----------
STOP_WORDS = set([
    "a", "an", "the", "is", "are", "was", "were", "in", "on", "of", "for", "to", "with",
    "and", "or", "but", "by", "as", "at", "from", "it", "this", "that", "these", "those",
    "be", "been", "has", "have", "had", "do", "does", "did", "not", "no", "yes", "i",
    "you", "he", "she", "we", "they", "them", "his", "her", "their", "our", "my", "me", 
    "your", "so", "if", "then", "there", "here", "when", "where", "why", "how", "can",
    "could", "would", "should", "will", "just", "also", "about", "all", "any", "some",
    "more", "most", "such", "only", "own", "same", "than", "too", "very"
])

# ---------- Helper: Sanitize URL for Directory/File Naming ----------
def sanitize_url(url):
    parsed = urlparse(url)
    path = parsed.path.replace('/', '_').strip('_')
    netloc = parsed.netloc.replace('.', '_')
    return f"{netloc}__{path or 'root'}"

# ---------- Upload Whoosh Index Files to GCS ----------
def upload_index_to_gcs(local_dir, gcs_prefix):
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    for file_name in os.listdir(local_dir):
        local_path = os.path.join(local_dir, file_name)
        blob = bucket.blob(f"{gcs_prefix}/{file_name}")
        blob.upload_from_filename(local_path)
        print(f"✅ Uploaded to GCS: {gcs_prefix}/{file_name}")


def send_index_ack(url, ack_type="completion", status="success", reason=None):
    """Send an ACK to the indexer_ack_queue with retries."""
    for attempt in range(3):
        try:
            ack_data = json.dumps({
                "url": url,
                "type": ack_type,
                "status": status,
                "reason": reason
            })
            r.rpush('indexer_ack_queue', ack_data)
            logging.info(f"[Indexer Node] ✅ Sent {ack_type} ACK for {url} (status: {status})")
            return
        except Exception as e:
            logging.error(f"[Indexer Node] ⚠️ Failed to send {ack_type} ACK for {url} (attempt {attempt+1}): {e}")
        time.sleep(1)
    logging.error(f"[Indexer Node] ❌ Failed to send {ack_type} ACK for {url} after 3 attempts")


# ---------- Celery Task ----------
@app.task(name='index_content')
def index_content(page_data):
    try:
        url = page_data.get("url")
        html = page_data.get("html")
        if not url or not html:
            print("[Indexer Node] ❌ Missing URL or HTML in page_data.")
            return

        send_index_ack(url, ack_type="receipt", status="success")
        print(f"\n[Indexer Node] 📥 Received Task for URL: {url}")

        soup = BeautifulSoup(html, 'html.parser')
        title = soup.title.get_text(strip=True) if soup.title else ''

        # Remove irrelevant tags
        for tag in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
            tag.decompose()

        content = ' '.join(tag.get_text(strip=True) for tag in soup.find_all([
            'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'li', 'span', 'div', 'td', 'th',
            'blockquote', 'summary', 'details', 'noscript'
        ]))

        # ---------- Local Whoosh Index ----------
        index_name = sanitize_url(url)
        local_index_path = os.path.join(BASE_INDEX_DIR, index_name)
        os.makedirs(local_index_path, exist_ok=True)

        schema = Schema(
            url=ID(stored=True, unique=True),
            title=TEXT(stored=True),
            content=TEXT(stored=True)
        )
        if not exists_in(local_index_path):
            create_in(local_index_path, schema)

        ix = open_dir(local_index_path)
        writer = ix.writer()
        writer.update_document(url=url, title=title, content=content)
        writer.commit()
        print(f"[Indexer Node] ✅ Whoosh indexed to {local_index_path}")

        # ---------- PostgreSQL Insert ----------
        words = content.lower().split()
        filtered_words = [word for word in words if word not in STOP_WORDS]
        freq_dict = dict(Counter(filtered_words))
        freq_json = json.dumps(freq_dict)

        try:
            conn = psycopg2.connect(
                host=DB_HOST, database=DB_NAME,
                user=DB_USER, password=DB_PASSWORD
            )
            cursor = conn.cursor()
            insert_query = """
                INSERT INTO pages (url, title, content, keywords)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING;
            """
            cursor.execute(insert_query, (url, title, content, freq_json))
            conn.commit()
            cursor.close()
            conn.close()
            print(f"[Indexer Node] ✅ Inserted into PostgreSQL: {url}")
        except Exception as db_err:
            print(f"[Indexer Node] ❌ DB insert failed: {db_err}")

        # ---------- GCS Upload ----------
        try:
            upload_index_to_gcs(local_index_path, f"whoosh_index/{index_name}")
        except Exception as gcs_err:
            print(f"[Indexer Node] ❌ GCS upload failed: {gcs_err}")

        send_index_ack(url, ack_type="completion", status="success")
    except Exception as e:
        print(f"[Indexer Node] ❌ Task failed: {e}")
        send_index_ack(url, ack_type="completion", status="failed", reason=str(e))