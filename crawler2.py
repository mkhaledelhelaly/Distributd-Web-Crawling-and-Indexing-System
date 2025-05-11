import time
import logging
import hashlib
from urllib.parse import urlparse, urljoin
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from celery import Celery
from urllib.robotparser import RobotFileParser
from google.cloud import storage
import redis
import json

# --- Configuration ---
GCS_BUCKET_NAME = "crawler-temp-storage"
DEFAULT_CRAWL_DELAY = 5
REDIS_HOST = '10.212.0.2'
REDIS_PORT = 6379

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Celery setup
app = Celery("crawler", broker=f"redis://{REDIS_HOST}:{REDIS_PORT}/0")

# Redis connection
r = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=0)

# Robots.txt and crawl-delay cache
robots_cache = {}
crawl_delays = {}

def send_ack(url, ack_type="completion", status="success", reason=None):
    """Send an ACK to the crawler_ack_queue with retries."""
    for attempt in range(3):
        try:
            ack_data = json.dumps({"url": url, "type": ack_type, "status": status, "reason": reason})
            r.rpush('crawler_ack_queue', ack_data)
            logging.info(f"✅ Sent {ack_type} ACK for {url} (status: {status})")
            return
        except Exception as e:
            logging.error(f"⚠️ Failed to send {ack_type} ACK for {url} (attempt {attempt+1}): {e}")
        time.sleep(1)
    logging.error(f"❌ Failed to send {ack_type} ACK for {url} after 3 attempts")

def is_allowed_by_robots(url):
    parsed = urlparse(url)
    domain = parsed.scheme + "://" + parsed.netloc
    if domain not in robots_cache:
        robots_url = urljoin(domain, "/robots.txt")
        rp = RobotFileParser()
        try:
            rp.set_url(robots_url)
            rp.read()
            robots_cache[domain] = rp
            logging.info(f"Fetched robots.txt from {robots_url}")
            content = requests.get(robots_url).text
            for line in content.splitlines():
                if "crawl-delay" in line.lower():
                    try:
                        delay = float(line.split(":")[1].strip())
                        crawl_delays[domain] = delay
                        logging.info(f"Crawl-delay set to {delay}s for {domain}")
                    except:
                        pass
        except Exception as e:
            logging.warning(f"Could not fetch robots.txt from {robots_url}: {e}")
            return True
    return robots_cache[domain].can_fetch("*", url)

def enforce_crawl_delay(domain):
    now = time.time()
    delay = crawl_delays.get(domain, DEFAULT_CRAWL_DELAY)
    last_access_key = f"last_access:{domain}"
    last_access = r.get(last_access_key)
    if last_access:
        elapsed = now - float(last_access)
        if elapsed < delay:
            sleep_time = delay - elapsed
            logging.info(f"Sleeping {sleep_time:.2f}s for crawl-delay on {domain}")
            time.sleep(sleep_time)
    r.set(last_access_key, str(time.time()))

def upload_html_to_gcs(bucket_name, url, html_content):
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename = f"{url_hash}_{timestamp}.html"
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(filename)
    blob.upload_from_string(html_content, content_type='text/html')
    if blob.exists():
        logging.info(f"✅ HTML for {url} saved to GCS as {filename}")
    else:
        logging.error(f"❌ GCS upload failed for {url}")

def extract_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for tag in soup.find_all("a", href=True):
        href = tag.get("href")
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.scheme in ("http", "https"):
            links.add(full_url)
    return links

@app.task(name="crawl_url")
def crawl_url(url, current_depth=0, max_depth=2):
    logging.info(f"🌐 Crawling URL: {url} (Depth: {current_depth}/{max_depth})")
    send_ack(url, ack_type="receipt", status="success")
    if current_depth > max_depth:
        logging.info(f"🚫 Reached max depth ({max_depth}) for {url}")
        send_ack(url, ack_type="completion", status="skipped", reason="max_depth")
        return
    if r.sismember("crawled_urls", url):
        logging.info(f"⏭ Already crawled: {url}")
        send_ack(url, ack_type="completion", status="skipped", reason="already_crawled")
        return
    if not is_allowed_by_robots(url):
        logging.warning(f"🚫 Blocked by robots.txt: {url}")
        send_ack(url, ack_type="completion", status="failed", reason="robots_blocked")
        return
    domain = urlparse(url).scheme + "://" + urlparse(url).netloc
    enforce_crawl_delay(domain)
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; MyCrawler/1.0)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html = response.text
        r.sadd("crawled_urls", url)
        upload_html_to_gcs(GCS_BUCKET_NAME, url, html)
        links = extract_links(html, url)
        logging.info(f"🔗 Found {len(links)} links in {url}")
        for link in links:
            if not r.sismember("crawled_urls", link):
                if current_depth + 1 <= max_depth:
                    app.send_task("enqueue_url", args=[link, current_depth + 1])
                    logging.info(f"➕ New crawl task added: {link} (Depth: {current_depth + 1})")
                else:
                    logging.info(f"🚫 Skipped {link} - max depth reached.")
        page_data = {"url": url, "html": html}
        r.rpush("pending_index_data", json.dumps(page_data))
        logging.info(f"📤 Sent to indexer: {url}")
        send_ack(url, ack_type="completion", status="success")
    except Exception as e:
        logging.error(f"❌ Error crawling {url}: {e}")
        send_ack(url, ack_type="completion", status="failed", reason=str(e))