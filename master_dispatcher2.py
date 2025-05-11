from celery import Celery
from celery.app.control import Inspect
from celery.app.control import Control
import redis
import time
import json
import threading
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Celery('dispatcher', broker='redis://10.212.0.2:6379/0')
r = redis.Redis(host='10.212.0.2', port=6379, db=0)

def get_active_workers(queue_name):
    """Get active workers based on the specific queue name."""
    control = Control(app)
    try:
        active = control.ping()
        workers = []
        if active:
            for worker_info in active:
                worker_id = list(worker_info.keys())[0]
                hostname = worker_id.split('@')[1]
                if queue_name.lower() in hostname.lower():
                    workers.append(hostname)
        workers.sort()
        if not workers:
            logging.warning(f"No active {queue_name} workers detected")
        else:
            logging.info(f"Active {queue_name} workers: {workers}")
        return workers
    except Exception as e:
        logging.error(f"Error retrieving active {queue_name} workers: {e}")
        return []

def wait_for_ack(expected_url, timeout=60, ack_type="completion"):
    """Waits for an ACK using BLPOP."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            ack = r.blpop('crawler_ack_queue', timeout=1)
            if ack:
                _, ack_data = ack
                try:
                    ack_data = json.loads(ack_data.decode())
                    if ack_data["url"] == expected_url and ack_data.get("type", "completion") == ack_type:
                        logging.info(f"[Dispatcher] ✅ {ack_type.capitalize()} ACK received for {expected_url} (status: {ack_data.get('status' )})")
                        return ack_data.get("status", "success"), ack_data.get("reason")
                    else:
                        r.rpush('crawler_ack_queue', json.dumps(ack_data))
                        logging.debug(f"[Dispatcher] Ignored ACK for {ack_data['url']} (expected {expected_url}, type {ack_type})")
                except Exception as e:
                    logging.error(f"[Dispatcher] ⚠️ Failed to parse ACK: {e}")
        except Exception as e:
            logging.error(f"[Dispatcher] ⚠️ Error reading crawler_ack_queue: {e}")
    logging.warning(f"[Dispatcher] ⏰ Timeout: No {ack_type} ACK for {expected_url}")
    return "timeout", None

def wait_for_index_ack(expected_url, timeout=60, ack_type="completion"):
    """Waits for an ACK from indexers using BLPOP."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            ack = r.blpop('indexer_ack_queue', timeout=1)
            if ack:
                _, ack_data = ack
                try:
                    ack_data = json.loads(ack_data.decode())
                    if ack_data["url"] == expected_url and ack_data.get("type", "completion") == ack_type:
                        logging.info(f"[Dispatcher] ✅ {ack_type.capitalize()} ACK received for {expected_url} (status: {ack_data.get('status')})")
                        return ack_data.get("status", "success"), ack_data.get("reason")
                    else:
                        # Push it back if it's not for this task
                        r.rpush('indexer_ack_queue', json.dumps(ack_data))
                        logging.debug(f"[Dispatcher] Ignored ACK for {ack_data['url']} (expected {expected_url}, type {ack_type})")
                except Exception as e:
                    logging.error(f"[Dispatcher] ⚠️ Failed to parse ACK: {e}")
        except Exception as e:
            logging.error(f"[Dispatcher] ⚠️ Error reading indexer_ack_queue: {e}")
    logging.warning(f"[Dispatcher] ⏰ Timeout: No {ack_type} ACK for {expected_url}")
    return "timeout", None


crawler_index = 0

def assign_crawler_tasks(max_batch=20):
    global crawler_index
    while True:
        try:
            url = r.lpop('pending_urls')
            if url is None:
                time.sleep(1)
                continue
            url = url.decode()
            logging.info(f"[Dispatcher]🔃🔃 Dispatching URL for crawling: {url}")
            for attempt in range(3):
                workers = get_active_workers("crawler")
                if not workers:
                    logging.warning("[Dispatcher] ❌❌ No active *CRAWLER* workers, retrying in 5s...")
                    time.sleep(5)
                    continue
                assigned = False
                num_workers = len(workers)
                worker = workers[crawler_index % num_workers]
                try:
                    app.send_task('crawl_url', args=[url], queue=worker, routing_key=worker)
                    logging.info(f"******** [Dispatcher] 🚀🚀 Sent {url} to {worker}, waiting for receipt ACK... *********")
                    receipt_status, _ = wait_for_ack(url, timeout=10, ack_type="receipt")
                    if receipt_status == "success":
                        logging.info(f"[Dispatcher]⌛⌛ Waiting for Crawler *COMPLETION* ACK for {url}...")
                        completion_status, reason = wait_for_ack(url, timeout=60, ack_type="completion")
                        if completion_status == "success":
                            assigned = True
                            crawler_index = (crawler_index + 1) % num_workers
                            logging.info(f"✅✅ CRAWLER successfully returned Completion ACK...")

                        elif completion_status in ("failed", "skipped"):
                            logging.warning(f"[Dispatcher] ⛔⛔Crawler Rejected Task {url} {completion_status}: {reason}")
                            assigned = True
                        else:
                            logging.warning(f"[Dispatcher] 👎👎No CRAWLER completion ACK, trying next worker..")
                            crawler_index = (crawler_index + 1) % num_workers
                    else:
                        logging.warning(f"[Dispatcher] No CRAWLER receipt ACK, trying next worker..")
                        crawler_index = (crawler_index + 1) % num_workers
                except Exception as e:
                    logging.error(f"[Dispatcher] ⚠️⚠️ Failed to assign to {worker}: {e}")
                    crawler_index = (crawler_index + 1) % num_workers
                if assigned:
                    break
                logging.warning(f"[Dispatcher] Could not assign {url} to {worker}, retrying with next worker...")
                time.sleep(2)
            else:
                logging.error(f"[Dispatcher] 🚨🚨 Failed to assign URL to a *CRAWLER*: {url}, re-queueing")
                r.rpush('pending_urls', url)
        except Exception as e:
            logging.error(f"[Dispatcher] 🚨 Error in crawler task assignment: {e}")
            time.sleep(5)

indexer_index =0
def assign_index_tasks(max_depth=20):
    global indexer_index
    while True:
        try:
            task_data = r.lpop('pending_index_data')
            if task_data is None:
                time.sleep(1)
                continue
            try:
                task_data = json.loads(task_data.decode())
            except Exception as e:
                logging.error(f"[Dispatcher] ❌ Failed to decode index task: {e}")
                time.sleep(1)
                continue
            logging.info(f"[Dispatcher] 📤📥 Dispatching **INDEX** task: {task_data['url']}")
            for attempt in range(3):
                workers = get_active_workers("indexer")
                if not workers:
                    logging.warning("[Dispatcher] ❌❌ No active INDEXERS — retrying in 5s...")
                    time.sleep(5)
                    continue
                assigned = False
                num_workers = len(workers)
                worker = workers[indexer_index % num_workers]
                try:
                    app.send_task('index_content', args=[task_data], queue=worker, routing_key=worker)
                    logging.info(f"[Dispatcher] ✈️✈️ Assigned task to {worker}, Waiting for receipt ACK...")
                    receipt_status, _ = wait_for_index_ack(task_data['url'],timeout=10, ack_type="receipt")
                    if receipt_status == "success":
                        
                        logging.info(f"[Dispatcher] 🕜🕜 Waiting for INDEXER COMPLETION ACK for {task_data['url']}...")
                        completion_status, reason = wait_for_index_ack(task_data['url'], timeout=60, ack_type="completion")
                        if completion_status =="success":
                            assigned = True
                            indexer_index = (indexer_index + 1) % num_workers
                            logging.info(f"☑️☑️ INDEXER successfully returned Completion ACK...")

                        elif completion_status in ("failed","skipped"):
                            logging.warning(f"[Dispatcher] Task {task_data['url']} {completion_status}: {reason}")
                            assigned = True
                        else: 
                            logging.warning(f"[Dispatcher] 😭😭 No INDEXER completion ACK, trying next worker")
                            indexer_index = (indexer_index + 1) % num_workers
                    else:
                        logging.warning(f"[Dispatcher] No INDEXER receipt ACK, trying next worker")
                        indexer_index = (indexer_index + 1) % num_workers
                except Exception as e:
                    logging.error(f"[Dispatcher] ⚠️ Failed to assign to {worker}: {e}")
                    indexer_index = (indexer_index + 1) % num_workers
                if assigned:
                    break
                logging.warning(f"[Dispatcher] Could not assign {task_data['url']} to {worker}, retrying with next worker...")
                time.sleep(2)
            else:
                logging.error(f"[Dispatcher] ✖️✖️ Failed to assign DATA to INDEXER: {task_data['url']}, re-queueing")
                r.rpush('pending_index_data', json.dumps(task_data))
        except Exception as e:
            logging.error(f"[Dispatcher] 🚨 Error in Indexer task assignment: {e}")
            time.sleep(5)


if __name__ == '__main__':
    logging.info("[Dispatcher] 🚀 Starting dispatcher...")
    # Create threads for crawler and indexer tasks
    crawler_thread = threading.Thread(target=assign_crawler_tasks, name="CrawlerThread", daemon=True)
    indexer_thread = threading.Thread(target=assign_index_tasks, name="IndexerThread", daemon=True)
    # Start threads
    crawler_thread.start()
    indexer_thread.start()
    # Keep the main thread alive
    try:
        while True:
            time.sleep(10)
            if not crawler_thread.is_alive():
                logging.error("[Dispatcher] Crawler thread stopped, restarting...")
                crawler_thread = threading.Thread(target=assign_crawler_tasks, name="CrawlerThread", daemon=True)
                crawler_thread.start()
            if not indexer_thread.is_alive():
                logging.error("[Dispatcher] Indexer thread stopped, restarting...")
                indexer_thread = threading.Thread(target=assign_index_tasks, name="IndexerThread", daemon=True)
                indexer_thread.start()
    except KeyboardInterrupt:
        logging.info("[Dispatcher] 🛑 Shutting down dispatcher...")