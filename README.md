# Distributed-Web-Crawling-and-Indexing-System
This project is a cloud-based distributed web crawling and search engine system built as part of a Computer Science course project. It consists of multiple components (master node, crawler nodes, indexer nodes, client, and database) working together using Celery, Redis, and Google Cloud infrastructure. The system crawls web pages, indexes content, and allows keyword-based search from a web interface.

Key features:
- Distributed crawling with politeness policy
- Content indexing with keyword frequency
- Queryable search engine frontend
- Task dispatching via Celery and Redis
- Fault-tolerant master node with load balancer failover
- Scalable and modular design

## ☁️ Google Cloud Platform (GCP) Setup

### 1. Google Cloud VMs Setup

Create the following **Virtual Machines (VMs)** in your Google Cloud project to set up the necessary components:

- **Client VM**: This will run the client component of your distributed web crawler.
- **Master Node VMs**: These two VMs (`master-node--vm1` and `master-node--vm2`) will run the master node for task dispatching and failover.
- **Crawler VMs**: These VMs (`crawler-1`, `crawler-2`, and `crawler-3`) will run the crawler tasks.
- **Indexer VM**: This VM (`indexer-1`,`indexer-2`) will handle the indexing tasks.

#### Steps to Create VMs:

1. In the GCP Console, go to the **Compute Engine** section.
2. Click on **Create Instance**.
3. Choose the **OS Image** (e.g., Ubuntu 20.04 LTS).
4. Under **Machine Type**, select the machine type based on your requirements (e.g., `e2-medium`).
5. Under **Identity and API access**, ensure **Allow default access** is selected.
6. Set the **Network Tag** for the VMs:
   - `master-dispatcher` for the master nodes
   - `crawler` for the crawler nodes
   - `indexer` for the indexer node
7. Set the **External IP** to **Ephemeral** or **Static**, depending on your use case.
8. Repeat the steps for each VM (`client`, `master-node--vm1`, `master-node--vm2`, `crawler-1`, `crawler-2`, `crawler-3`, `indexer-1`,`indexer-2`).

---

## 2. Set Up Python Environment on VMs

### Install Python 3.8+ if it's not already installed
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv


```
### Create a virtual environmen
```bash
python3 -m venv venv

```
### Activate the virtual environment
source venv/bin/activate

### Install required Python dependencies
pip install -r requirements.txt

## ⚙️ Installation Instructions


### 2. Set Up Python Environment

Ensure Python 3.8+ is installed. Then create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate

```

### 3. Install Redis

```bash
sudo apt update
sudo apt install redis-server
sudo systemctl enable redis
sudo systemctl start redis

If Redis is to be accessed across VMs, configure it to allow external access:

Edit the file /etc/redis/redis.conf and change the line:
bind 127.0.0.1
to 
bind 0.0.0.0

```
### 4. Install Celery

```bash
pip install celery
```
### 5. Install Python Dependencies
```bash
pip install beautifulsoup4 requests google-cloud-storage nltk
pip install whoosh
```
## Crawler Setup
### 1. Directory Structure
On each of the crawler VMs (crawler-1, crawler-2, crawler-3), 
create a directory for the crawler component:
```bash
mkdir ~/distributed-crawler
cd ~/distributed-crawler
```
### Creating the `crawler2.py` Script
Download the crawler2.py script from the repository or create it manually inside the ~/distributed-crawler directory. The script is responsible for crawling web pages, extracting content, and uploading the data to Google Cloud Storage (GCS).
### 3. Run the Crawler Script
Once the `crawler2.py` script is in place, you can run it on the crawler VMs:
-Ensure that the virtual environment is activated:
```bash
source ~/distributed-crawler/venv/bin/activate
```
Start the crawler script:
```bash
celery -A crawler2 worker --loglevel=info --hostname=crawler-1 --queues=crawler-1
```
### 4. Running Multiple Crawler Instances
Each crawler VM (crawler-1, crawler-2, crawler-3) will run a separate instance of the crawler2.py script, enabling distributed crawling across multiple nodes.

## Indexer Setup
### 1. Directory Structure
On each of the indexer VMs (indexer-1, indexer-2), 
create a directory for the indexer component:
```bash
mkdir ~/distributed-crawler
cd ~/distributed-crawler
```
### 2. Creating the `indexer.py` Script
Download the `indexer.py` script from the repository or create it manually inside the `~/distributed-crawler` directory. The script is responsible for processing the crawled data, indexing content based on keyword frequencies, and uploading the indexed data to Google Cloud Storage (GCS).

### 3. Run the indexer Script
 celery -A indexer worker --loglevel=info --hostname=indexer-1 --queues=indexer-1 --concurrency=1

### 4. Running Multiple Indexer Instances
Each indexer VM (indexer-1, indexer-2) will run a separate instance of the `indexer.py` script. This enables distributed indexing across multiple nodes, allowing the system to handle a higher volume of data and ensure fault tolerance in case of failure.

## master-vm--1 setup
### 1. Directory Structure 
Create a directory for the master-vm component:
```bash
mkdir ~/distributed-crawler
cd ~/distributed-crawler
```
### 2. Creating the `master_dispatcher2.py` Script
Download the `master_dispatcher2.py` script from the repository or create it manually inside the `~/distributed-crawler` directory. The script is responsible for managing the distributed crawling and indexing workflow. It dispatches crawl tasks to the crawler nodes, manages task queues with Redis, and handles the overall coordination of the master node.

### 3. Creating the `master_receiver.py` Script
Download the `master_receiver.py` script from the repository or create it manually inside the `~/distributed-crawler` directory. The script is responsible for receiving and processing the tasks dispatched by the `master_dispatcher2.py` script. It listens for task results from the crawler nodes and ensures that the data is correctly indexed and stored in Google Cloud Storage (GCS).

### 4. Running the master node
open 2 SSHs for the node

1st SSH:
```bash
python3 master_dispatcher2.py
```

2nd SSH

```bash
celery -A master_receiver worker --loglevel=info --concurrency=1
```

## Client setup
### 1. Directory Structure 
Create a directory for the client component:
```bash
mkdir ~/client
cd ~/client
```
Install flask dependencies
### 2. Creating the `app.py` Script (Client)

Download the `app.py` script from the repository or create it manually inside the `~/distributed-crawler` directory. The `app.py` script is the client-side component of the distributed web crawling system. It is responsible for sending search queries to the system and displaying the results. It interacts with the indexed data, retrieves relevant results from Google Cloud Storage (GCS), and provides a simple web interface for users to search and view the results.

### 3. make template directory for gui
```bash
mkdir /client/template
```
#### 3.1 creating the `index.html`
Download the `index.html` template from the repository or create it manually inside the `~/distributed-crawler/templates` directory. The `index.html` template provides the front-end structure for the user to interact with the search engine. It contains the search input field and a section for displaying the search results returned by the backend.


## 💻 Troubleshooting

If you encounter any issues while setting up or running the system, try the following:

Check logs: Look at the logs for each node (crawler, indexer, master) to identify errors.

Ensure network connectivity: Make sure that your Google Cloud VMs can communicate with each other, especially for Redis and Celery.

Redis and Celery issues: Restart Redis or Celery if tasks aren't being dispatched correctly.

For more advanced troubleshooting, refer to the official documentation of Redis, Celery, and Google Cloud.

