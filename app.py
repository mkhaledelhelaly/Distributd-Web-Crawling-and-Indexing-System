                                                                                                                                                                                                                                                                          
from flask import Flask, render_template, request, redirect, url_for
from celery import Celery
import redis
import os
from google.cloud import storage
from whoosh.index import open_dir
from whoosh.qparser import MultifieldParser

# --- Configuration ---
REDIS_HOST = '10.212.0.2'
REDIS_PORT = 6379
CELERY_BROKER = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
GCS_BUCKET_NAME = 'my-index-bucket'
LOCAL_INDEX_DIR = 'indexdir'

# Initialize Flask app
app = Flask(__name__)

# Initialize Celery app
celery = Celery("crawler", broker=CELERY_BROKER)

# Connect to Redis
r = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=0)

# ---------- GCS Index Download ----------
def download_index_from_gcs(bucket_name, local_base_dir):
    os.makedirs(local_base_dir, exist_ok=True)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix="whoosh_index/"))

    for blob in blobs:
        parts = blob.name.split('/')
        if len(parts) != 3:
            continue
        _, subdir, filename = parts
        local_subdir = os.path.join(local_base_dir, subdir)
        os.makedirs(local_subdir, exist_ok=True)
        local_path = os.path.join(local_subdir, filename)
        if not os.path.exists(local_path):
            blob.download_to_filename(local_path)

# ---------- Whoosh Search ----------
def search_across_all_indexes(index_base_dir, query_str):
    results_list = []
    subdirs = [os.path.join(index_base_dir, name) for name in os.listdir(index_base_dir)
               if os.path.isdir(os.path.join(index_base_dir, name))]

    keywords = [word.lower() for word in query_str.split()]

    for subdir in subdirs:
        try:
            ix = open_dir(subdir)
            with ix.searcher() as searcher:
                parser = MultifieldParser(["title", "content"], schema=ix.schema)
                query = parser.parse(query_str)
                results = searcher.search(query, limit=3)
                for hit in results:
                    url = hit['url']
                    # Count how many keywords appear in the URL
                    match_count = sum(1 for kw in keywords if kw in url.lower())
                    results_list.append({'title': hit['title'], 'url': url, 'match_count': match_count})
        except Exception as e:
            continue  # Skip index errors

    # Sort results by match_count DESCENDING, then by title alphabetically
    sorted_results = sorted(results_list, key=lambda x: (-x['match_count'], x['title']))

    # Remove 'match_count' before returning to template
    final_results = [{'title': r['title'], 'url': r['url']} for r in sorted_results]

    return final_results

# --- Flask Routes ---
@app.route("/", methods=["GET", "POST"])
def index():
    search_results = []
    search_query = request.args.get('search', '')
    crawled_urls = [u.decode() for u in r.smembers("crawled_urls")]

    if request.method == "POST":
        if "url" in request.form:
            # Handle URL submission for crawling with depth
            url = request.form["url"]
            depth = request.form.get("depth", 1)
            if url:
                celery.send_task("enqueue_url", args=[url, int(depth)])
            return redirect(url_for("index"))

        if "download_indexes" in request.form:
            # Handle download indexes button
            download_index_from_gcs(GCS_BUCKET_NAME, LOCAL_INDEX_DIR)
            return redirect(url_for("index"))

    # If a search query exists, run Whoosh search
    if search_query:
        search_results = search_across_all_indexes(LOCAL_INDEX_DIR, search_query)

    return render_template("index.html",
                           crawled_urls=crawled_urls,
                           search_query=search_query,
                           search_results=search_results)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
