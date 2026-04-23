# IP Info

A Flask app for IP and domain/email intelligence with background jobs, status tracking, and HTML or JSON results.

## Get from GitHub

1. Clone the repository:
  git clone https://github.com/artofscripting/ip-info.git

2. Enter the project folder:
  cd ip-info

3. Pull the latest updates later:
  git pull origin main

## Run with Docker

1. Pull the image:
   docker pull artofscripting/ip-info:latest

2. Start the container on port 1444:
   docker run -d --name ip-info-app -p 1444:1444 artofscripting/ip-info:latest

3. Open the app:
   https://localhost:1444

4. View logs:
   docker logs -f ip-info-app

5. Stop and remove:
   docker rm -f ip-info-app

## Run with Python

1. Create and activate a virtual environment (Windows PowerShell):
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

2. Install dependencies:
   pip install -r requirements.txt

3. Start the app:
   python main.py

4. Open:
   https://localhost:1444

## Main Endpoints

### /ip

IP analysis endpoint.

- HTML mode (default):
  /ip?ip=8.8.8.8

- JSON mode:
  /ip?ip=8.8.8.8&type=json

What it does:
- Creates a background job.
- Checks reputation and blacklist sources.
- Returns either an HTML report job (default) or JSON job response.

### /email

Domain/email analysis endpoint.

- HTML mode (default):
  /email?email=example.com

- JSON mode:
  /email?email=example.com&type=json

What it does:
- Normalizes email/domain input.
- Creates a background job.
- Resolves WHOIS, DNS, SPF, MX, and related intelligence.

### /check

Job status/result endpoint.

- Status/result by job id:
  /check?jobid=<JOB_ID>

- JSON status payload:
  /check?jobid=<JOB_ID>&type=json

- Status-only lightweight check:
  /check?jobid=<JOB_ID>&status_only=1

If the job is not finished, this endpoint returns current status. If finished, it returns the stored result payload.

## How Jobs Work

Jobs are tracked in a local SQLite database (jobs.db) with fields such as:
- job_id
- endpoint
- target
- result_format
- status
- result_payload
- error_message
- created_at / updated_at

Typical lifecycle:
1. queued
2. running
3. done (or error)

A request to /ip or /email creates a job and returns a job id/check URL. Worker threads process the job in the background and store final output in the database.

## What type=json Does

Adding type=json changes the output format behavior:

- On /ip and /email:
  The endpoint queues a JSON job and returns a JSON response containing:
  - jobid
  - status (queued)
  - check_url

- On /check:
  If the job is done and its result format is JSON, /check returns parsed JSON data.
  If not done yet, /check returns a JSON status payload.

Notes:
- Without type=json, /ip and /email default to HTML-focused behavior.
- For HTML report runs, the app can also create a companion finished JSON job for the same target.
