
import whois
import dns.resolver
import webtech
import datetime
from flask import Flask, send_file, jsonify
from flask import request
import os
import os
import sys
import urllib3
import argparse
import re
import socket
import dns
import warnings
import threading
from concurrent.futures import ThreadPoolExecutor
from dns import resolver
from requests import get
import pydnsbl
from urlcache import URLCache
#from datetime import datetime

warnings.filterwarnings("ignore", category=DeprecationWarning)


import sys
import socket
from datetime import datetime

app = Flask(__name__)

import requests
import json
import time
import sqlite3
import uuid
import html
from urllib.parse import urlparse

CACHE_SECONDS = 3600
URL_CHECK_WORKERS = 100
MEMORY_CACHE_SECONDS = 60 * 60
FEED_CHECK_CACHE_SECONDS = 60 * 60
BLACKLIST_STATUS_CACHE_SECONDS = 60 * 60
FEED_ENTRY_COUNT_CACHE_SECONDS = 60 * 60
JOB_DB_PATH = "jobs.db"
url_cache = URLCache(db_path="urlcache.db", expiration_seconds=CACHE_SECONDS)
memory_cache = {}
memory_cache_lock = threading.Lock()
feed_check_cache = {}
feed_check_cache_lock = threading.Lock()
feed_entry_count_cache = {}
feed_entry_count_cache_lock = threading.Lock()
blacklist_status_cache = {}
blacklist_status_cache_lock = threading.Lock()
job_db_lock = threading.Lock()


def init_job_db():
    with sqlite3.connect(JOB_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                target TEXT NOT NULL,
                result_format TEXT NOT NULL,
                status TEXT NOT NULL,
                result_payload TEXT,
                error_message TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.commit()


def create_job(endpoint, target, result_format):
    now = time.time()
    job_id = str(uuid.uuid4())
    with job_db_lock:
        with sqlite3.connect(JOB_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO jobs (job_id, endpoint, target, result_format, status, result_payload, error_message, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, str(endpoint), str(target), str(result_format), "queued", None, None, now, now),
            )
            conn.commit()
    return job_id


def update_job_status(job_id, status, result_payload=None, error_message=None):
    now = time.time()
    with job_db_lock:
        with sqlite3.connect(JOB_DB_PATH) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, result_payload = ?, error_message = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (str(status), result_payload, error_message, now, str(job_id)),
            )
            conn.commit()


def get_job(job_id):
    with sqlite3.connect(JOB_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT job_id, endpoint, target, result_format, status, result_payload, error_message, created_at, updated_at FROM jobs WHERE job_id = ?",
            (str(job_id),),
        ).fetchone()
    return dict(row) if row else None


def get_recent_jobs(limit=20):
    with sqlite3.connect(JOB_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT job_id, endpoint, target, result_format, status, error_message, created_at, updated_at
            FROM jobs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def create_finished_json_job(endpoint, target, payload_obj):
    json_job_id = create_job(endpoint, target, "json")
    try:
        payload_text = json.dumps(payload_obj)
    except Exception:
        payload_text = json.dumps({"error": "failed to serialize JSON payload"})
    update_job_status(json_job_id, "done", result_payload=payload_text)
    return json_job_id


def render_jobs_modal():
    jobs = get_recent_jobs()
    rows = []
    for job in jobs:
        created_text = datetime.fromtimestamp(job["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        target_text = html.escape(str(job["target"]))
        error_text = html.escape(str(job["error_message"] or ""))
        status_html = html.escape(str(job["status"])) + ("<br><span class='jobs-error'>" + error_text + "</span>" if error_text else "")
        result_url = "/check?jobid=" + html.escape(str(job["job_id"]))
        if str(job.get("result_format")) == "json":
            result_url = result_url + "&type=json"
        is_done = str(job.get("status")) == "done"
        action_text = "Open Result" if is_done else "Check Status"
        action_href = result_url if is_done else "/check?jobid=" + html.escape(str(job["job_id"]))
        rows.append(
            "<tr>"
            "<td>" + html.escape(str(job["endpoint"])) + "</td>"
            "<td>" + target_text + "</td>"
            "<td>" + html.escape(str(job["result_format"])) + "</td>"
            "<td class='jobs-status'>" + status_html + "</td>"
            "<td>" + created_text + "</td>"
            "<td><a class='jobs-action' href='" + action_href + "'>" + action_text + "</a></td>"
            "</tr>"
        )

    table_rows = "".join(rows) if rows else "<tr><td colspan='6'>No jobs yet</td></tr>"
    return (
        "<style>"
        ".jobs-launch{position:fixed;top:16px;right:16px;z-index:1000;background:#5eb2ff;color:#032147;border:none;border-radius:999px;padding:10px 16px;font-weight:700;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.28);}"
        ".jobs-modal{display:none;position:fixed;inset:0;z-index:1001;background:rgba(2,12,31,.72);padding:24px;overflow:auto;}"
        ".jobs-modal.open{display:block;}"
        ".jobs-dialog{max-width:1100px;margin:24px auto;background:#0d2f66;color:#e7f1ff;border:1px solid #2a6fb8;border-radius:18px;box-shadow:0 16px 50px rgba(0,0,0,.35);padding:18px;}"
        ".jobs-header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px;}"
        ".jobs-title{margin:0;color:#e7f1ff;}"
        ".jobs-close{background:#123d7f;color:#e7f1ff;border:1px solid #2a6fb8;border-radius:10px;padding:8px 12px;cursor:pointer;}"
        ".jobs-table{width:100%;border-collapse:collapse;background:#0a244f;border-radius:12px;overflow:hidden;}"
        ".jobs-table th,.jobs-table td{border:1px solid #1f4e89;padding:8px 10px;text-align:left;vertical-align:top;}"
        ".jobs-table th{background:#124382;color:#dff0ff;}"
        ".jobs-table a{color:#5eb2ff;}"
        ".jobs-note{margin:0 0 12px 0;color:#dff0ff;}"
        ".jobs-error{color:#facc15;display:inline-block;margin-top:4px;}"
        "</style>"
        "<a class='jobs-launch' href='/check'>Jobs</a>"
        "<div id='jobs-modal' class='jobs-modal' onclick='if(event.target===this){closeJobsModal();}'>"
        "<div class='jobs-dialog'>"
        "<div class='jobs-header'><h2 class='jobs-title'>Recent Jobs</h2><button type='button' class='jobs-close' onclick='closeJobsModal()'>Close</button></div>"
        "<p class='jobs-note'>Review queued, running, completed, or failed jobs and jump to their status or final result.</p>"
        "<table class='jobs-table'>"
        "<tr><th>Endpoint</th><th>Target</th><th>Format</th><th>Status</th><th>Created</th><th>Action</th></tr>"
        + table_rows +
        "</table>"
        "</div>"
        "</div>"
        "<script>"
        "function openJobsModal(){var modal=document.getElementById('jobs-modal');if(modal){modal.classList.add('open');}}"
        "function closeJobsModal(){var modal=document.getElementById('jobs-modal');if(modal){modal.classList.remove('open');}}"
        "</script>"
    )


def run_ip_job(job_id, ip_value, result_format):
    update_job_status(job_id, "running")
    try:
        if result_format == "json":
            payload = json.dumps(get_ip_json(ip_value))
            update_job_status(job_id, "done", result_payload=payload)
        else:
            payload = get_IP_info(ip_value)

            # Mark HTML job complete immediately, then create the JSON companion in background.
            update_job_status(job_id, "done", result_payload=payload)

            def create_ip_json_companion():
                try:
                    json_payload = get_ip_json(ip_value)
                    create_finished_json_job("ip", ip_value, json_payload)
                except Exception:
                    pass

            threading.Thread(target=create_ip_json_companion, daemon=True).start()
    except Exception as e:
        update_job_status(job_id, "error", error_message=str(e))


def run_email_job(job_id, domain_name, result_format):
    update_job_status(job_id, "running")
    try:
        if result_format == "json":
            payload = json.dumps(get_email_json(domain_name))
        else:
            payload = get_email_info(domain_name)
            # For HTML reports, also persist a completed JSON companion job.
            json_payload = get_email_json(domain_name)
            create_finished_json_job("email", domain_name, json_payload)
        update_job_status(job_id, "done", result_payload=payload)
    except Exception as e:
        update_job_status(job_id, "error", error_message=str(e))


def build_memory_cache_key(url, headers=None, data=None, verify=True, user_agent=None):
    headers_key = json.dumps(headers, sort_keys=True, default=str) if headers is not None else None
    data_key = json.dumps(data, sort_keys=True, default=str) if data is not None else None
    return str(url), headers_key, data_key, bool(verify), str(user_agent) if user_agent is not None else None


def cached_get_text(url, headers=None, data=None, verify=True, user_agent=None):
    key = build_memory_cache_key(url, headers=headers, data=data, verify=verify, user_agent=user_agent)
    now = time.time()

    with memory_cache_lock:
        cached_entry = memory_cache.get(key)
        if cached_entry and cached_entry["expires_at"] > now:
            return cached_entry["value"]
        if cached_entry:
            memory_cache.pop(key, None)

    value = url_cache.get(url, headers=headers, data=data, verify=verify, user_agent=user_agent)

    with memory_cache_lock:
        memory_cache[key] = {
            "value": value,
            "expires_at": now + MEMORY_CACHE_SECONDS,
        }

    return value


def cached_get_json(url, headers=None, data=None, verify=True, user_agent=None):
    response_text = cached_get_text(url, headers=headers, data=data, verify=verify, user_agent=user_agent)
    if isinstance(response_text, bytes):
        response_text = response_text.decode("utf-8", errors="replace")
    return json.loads(response_text)


def cached_feed_check(url, badip):
    key = (str(url), str(badip))
    now = time.time()

    with feed_check_cache_lock:
        cached_entry = feed_check_cache.get(key)
        if cached_entry and cached_entry["expires_at"] > now:
            return cached_entry["value"]
        if cached_entry:
            feed_check_cache.pop(key, None)

    value = content_test(url, badip)

    with feed_check_cache_lock:
        feed_check_cache[key] = {
            "value": value,
            "expires_at": now + FEED_CHECK_CACHE_SECONDS,
        }

    return value


def cached_feed_entry_count(url):
    import ipaddress

    key = str(url)
    now = time.time()

    with feed_entry_count_cache_lock:
        cached_entry = feed_entry_count_cache.get(key)
        if cached_entry and cached_entry["expires_at"] > now:
            return cached_entry["value"]
        if cached_entry:
            feed_entry_count_cache.pop(key, None)

    count = 0
    try:
        html_content = cached_get_text(url)
        if isinstance(html_content, bytes):
            html_content = html_content.decode("utf-8", errors="replace")

        for line in html_content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            line = line.split("#")[0].strip().split(";")[0].strip()
            if not line:
                continue
            line = line.split()[0]
            if not line:
                continue
            try:
                if "/" in line:
                    ipaddress.ip_network(line, strict=False)
                    count = count + 1
                else:
                    ipaddress.ip_address(line)
                    count = count + 1
            except ValueError:
                continue
    except Exception:
        count = 0

    with feed_entry_count_cache_lock:
        feed_entry_count_cache[key] = {
            "value": count,
            "expires_at": now + FEED_ENTRY_COUNT_CACHE_SECONDS,
        }

    return count


def log_email_progress(domain_name, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [email] {domain_name}: {message}", flush=True)


def check_ip_blacklist_status(ip_value):
    key = ("ip_blacklist", str(ip_value))
    now = time.time()

    with blacklist_status_cache_lock:
        cached_entry = blacklist_status_cache.get(key)
        if cached_entry and cached_entry["expires_at"] > now:
            return cached_entry["value"]
        if cached_entry:
            blacklist_status_cache.pop(key, None)

    try:
        ip_checker = pydnsbl.DNSBLIpChecker()
        res = ip_checker.check(str(ip_value))
        value = bool(res.blacklisted)
    except Exception:
        value = None

    with blacklist_status_cache_lock:
        blacklist_status_cache[key] = {
            "value": value,
            "expires_at": now + BLACKLIST_STATUS_CACHE_SECONDS,
        }

    return value


def cached_dnsbl_status(ip_value, bl):
    key = ("dnsbl", str(ip_value), str(bl))
    now = time.time()

    with blacklist_status_cache_lock:
        cached_entry = blacklist_status_cache.get(key)
        if cached_entry and cached_entry["expires_at"] > now:
            return cached_entry["value"]
        if cached_entry:
            blacklist_status_cache.pop(key, None)

    my_resolver = dns.resolver.Resolver()
    query = '.'.join(reversed(str(ip_value).split("."))) + "." + bl
    my_resolver.timeout = 1
    my_resolver.lifetime = 1
    try:
        answers = my_resolver.resolve(query, "A")
        answer_txt = my_resolver.resolve(query, "TXT")
        value = {
            "status": "listed",
            "answer": str(answers[0]),
            "txt": str(answer_txt[0]),
        }
    except dns.resolver.NXDOMAIN:
        value = {
            "status": "not listed",
            "answer": None,
            "txt": None,
        }
    except dns.resolver.Timeout:
        value = {
            "status": "timeout",
            "answer": None,
            "txt": None,
        }
    except dns.resolver.NoNameservers:
        value = {
            "status": "no nameservers",
            "answer": None,
            "txt": None,
        }
    except dns.resolver.NoAnswer:
        value = {
            "status": "no answer",
            "answer": None,
            "txt": None,
        }
    except Exception:
        value = {
            "status": "error",
            "answer": None,
            "txt": None,
        }

    with blacklist_status_cache_lock:
        blacklist_status_cache[key] = {
            "value": value,
            "expires_at": now + BLACKLIST_STATUS_CACHE_SECONDS,
        }

    return value


def ip_to_link(value):
    import ipaddress
    try:
        ip_value = str(ipaddress.ip_address(str(value)))
        return "<a target=new href='/ip?ip=" + ip_value + "'>" + ip_value + "</a>"
    except Exception:
        return str(value)

bls = ["b.barracudacentral.org", "bl.spamcop.net",
       "blacklist.woody.ch", "cbl.abuseat.org",
       "combined.abuse.ch", "combined.rbl.msrbl.net",
       "db.wpbl.info", "dnsbl.cyberlogic.net",
       "dnsbl.sorbs.net", "drone.abuse.ch", "drone.abuse.ch",
       "duinv.aupads.org", "dul.dnsbl.sorbs.net", "dul.ru",
       "dynip.rothen.com",
       "http.dnsbl.sorbs.net", "images.rbl.msrbl.net",
       "ips.backscatterer.org", "ix.dnsbl.manitu.net",
       "korea.services.net", "misc.dnsbl.sorbs.net",
       "noptr.spamrats.com", "ohps.dnsbl.net.au", "omrs.dnsbl.net.au",
       "osps.dnsbl.net.au", "osrs.dnsbl.net.au",
       "owfs.dnsbl.net.au", "pbl.spamhaus.org", "phishing.rbl.msrbl.net",
       "probes.dnsbl.net.au", "proxy.bl.gweep.ca", "rbl.interserver.net",
       "rdts.dnsbl.net.au", "relays.bl.gweep.ca", "relays.nether.net",
       "residential.block.transip.nl", "ricn.dnsbl.net.au",
       "rmst.dnsbl.net.au", "smtp.dnsbl.sorbs.net",
       "socks.dnsbl.sorbs.net", "spam.abuse.ch", "spam.dnsbl.sorbs.net",
       "spam.rbl.msrbl.net", "spam.spamrats.com", "spamrbl.imp.ch",
       "t3direct.dnsbl.net.au", "tor.dnsbl.sectoor.de",
       "torserver.tor.dnsbl.sectoor.de", "ubl.lashback.com",
       "ubl.unsubscore.com", "virus.rbl.jp", "virus.rbl.msrbl.net",
       "web.dnsbl.sorbs.net", "wormrbl.imp.ch", "xbl.spamhaus.org",
       "zen.spamhaus.org", "zombie.dnsbl.sorbs.net"]

DISABLED_DNSBLS = {
    "db.wpbl.info",
    "ix.dnsbl.manitu.net",
    "rmst.dnsbl.net.au",
    "tor.dnsbl.sectoor.de",
    "torserver.tor.dnsbl.sectoor.de",
}

URLS = [
    #EmergingThreats
    ('http://rules.emergingthreats.net/blockrules/compromised-ips.txt',
     'is not listed on EmergingThreats',
     'is listed on EmergingThreats',
     True),

    #AlienVault
    ('http://reputation.alienvault.com/reputation.data',
     'is not listed on AlienVault',
     'is listed on AlienVault',
     True),

    #BlocklistDE
    ('http://www.blocklist.de/lists/bruteforcelogin.txt',
     'is not listed on BlocklistDE',
     'is listed on BlocklistDE',
     True),

    #Feodo
    ('http://rules.emergingthreats.net/blockrules/compromised-ips.txt',
     'is not listed on Feodo',
     'is listed on Feodo',
     True),

    #Abuse.ch Feodo Tracker IP blocklist
    ('https://feodotracker.abuse.ch/downloads/ipblocklist.txt',
     'is not listed on Abuse.ch Feodo Tracker',
     'is listed on Abuse.ch Feodo Tracker',
     True),

    #Abuse.ch SSLBL IP blacklist
    ('https://sslbl.abuse.ch/blacklist/sslipblacklist.txt',
     'is not listed on Abuse.ch SSLBL',
     'is listed on Abuse.ch SSLBL',
     True),

    #CINS Army bad IP list
    ('https://cinsscore.com/list/ci-badguys.txt',
     'is not listed on CINS Army',
     'is listed on CINS Army',
     True),

    #Spamhaus DROP
    ('https://www.spamhaus.org/drop/drop.txt',
     'is not listed on Spamhaus DROP',
     'is listed on Spamhaus DROP',
     True),

    #Spamhaus EDROP
    ('https://www.spamhaus.org/drop/edrop.txt',
     'is not listed on Spamhaus EDROP',
     'is listed on Spamhaus EDROP',
     True),

    #FireHOL Level 1 netset
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset',
     'is not listed on FireHOL Level 1',
     'is listed on FireHOL Level 1',
     True),

    #Emerging Threats botcc
    ('https://rules.emergingthreats.net/fwrules/emerging-Block-IPs.txt',
     'is not listed on Emerging Threats botcc',
     'is listed on Emerging Threats botcc',
     True),

    #Greensnow
    ('https://blocklist.greensnow.co/greensnow.txt',
     'is not listed on Greensnow',
     'is listed on Greensnow',
     True),

    #FireHOL Level 2
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level2.netset',
     'is not listed on FireHOL Level 2',
     'is listed on FireHOL Level 2',
     True),

    #FireHOL Level 3
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level3.netset',
     'is not listed on FireHOL Level 3',
     'is listed on FireHOL Level 3',
     True),

    #FireHOL Level 4
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level4.netset',
     'is not listed on FireHOL Level 4',
     'is listed on FireHOL Level 4',
     True),

    #FireHOL Abusers (all)
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_abusers_1d.netset',
     'is not listed on FireHOL Abusers 1d',
     'is listed on FireHOL Abusers 1d',
     True),

    #FireHOL Abusers 30d
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_abusers_30d.netset',
     'is not listed on FireHOL Abusers 30d',
     'is listed on FireHOL Abusers 30d',
     True),

    #FireHOL Anonymous (VPN/Proxy/Tor aggregation)
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_anonymous.netset',
     'is not listed on FireHOL Anonymous',
     'is listed on FireHOL Anonymous',
     True),

    #FireHOL Webclient
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_webclient.netset',
     'is not listed on FireHOL Webclient',
     'is listed on FireHOL Webclient',
     True),

    #Tor Exit Nodes
    ('https://check.torproject.org/torbulkexitlist',
     'is not listed on Tor Exit Nodes',
     'is listed on Tor Exit Nodes',
     True),

    #Blocklist.de SSH
    ('http://www.blocklist.de/lists/ssh.txt',
     'is not listed on Blocklist.de SSH',
     'is listed on Blocklist.de SSH',
     True),

    #Blocklist.de SMTP
    ('http://www.blocklist.de/lists/mail.txt',
     'is not listed on Blocklist.de SMTP',
     'is listed on Blocklist.de SMTP',
     True),

    #IPsum (threat intelligence, tab-delimited IP + score)
    ('https://raw.githubusercontent.com/stamparm/ipsum/master/ipsum.txt',
     'is not listed on IPsum',
     'is listed on IPsum',
     True),

    #Abuse.ch Feodo Tracker Aggressive
    ('https://feodotracker.abuse.ch/downloads/ipblocklist_aggressive.txt',
     'is not listed on Abuse.ch Feodo Tracker Aggressive',
     'is listed on Abuse.ch Feodo Tracker Aggressive',
     True),

    #Team Cymru Full Bogons
    ('https://www.team-cymru.org/Services/Bogons/fullbogons-ipv4.txt',
     'is not listed on Team Cymru Full Bogons',
     'is listed on Team Cymru Full Bogons',
     True),

    #Blocklist.de All
    ('http://www.blocklist.de/lists/all.txt',
     'is not listed on Blocklist.de All',
     'is listed on Blocklist.de All',
     True),

    #Blocklist.de Apache
    ('http://www.blocklist.de/lists/apache.txt',
     'is not listed on Blocklist.de Apache',
     'is listed on Blocklist.de Apache',
     True),

    #Blocklist.de Bots
    ('http://www.blocklist.de/lists/bots.txt',
     'is not listed on Blocklist.de Bots',
     'is listed on Blocklist.de Bots',
     True),

    #Blocklist.de SIP
    ('http://www.blocklist.de/lists/sip.txt',
     'is not listed on Blocklist.de SIP',
     'is listed on Blocklist.de SIP',
     True),

    #Blocklist.de Strong IPs (multiple violations)
    ('http://www.blocklist.de/lists/strongips.txt',
     'is not listed on Blocklist.de StrongIPs',
     'is listed on Blocklist.de StrongIPs',
     True),

    #Binary Defense banlist
    ('https://www.binarydefense.com/banlist.txt',
     'is not listed on Binary Defense',
     'is listed on Binary Defense',
     True),

    #StopForumSpam toxic CIDRs
    ('https://www.stopforumspam.com/downloads/toxic_ip_cidr.txt',
     'is not listed on StopForumSpam Toxic',
     'is listed on StopForumSpam Toxic',
     True),

    #Dan.me.uk Tor exit nodes
    ('https://www.dan.me.uk/torlist/',
     'is not listed on Dan.me.uk Tor',
     'is listed on Dan.me.uk Tor',
     True),

    #VoIPBL (SIP/VoIP attackers)
    ('http://www.voipbl.org/update/',
     'is not listed on VoIPBL',
     'is listed on VoIPBL',
     True),

    #DShield 1d (SANS Internet Storm Center)
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/dshield_1d.netset',
     'is not listed on DShield 1d',
     'is listed on DShield 1d',
     True),

    #Etnetera Aggressive (Czech CERT)
    ('https://security.etnetera.cz/feeds/etn_aggressive.txt',
     'is not listed on Etnetera Aggressive',
     'is listed on Etnetera Aggressive',
     True),

    #Blocklist.de Postfix
    ('http://www.blocklist.de/lists/postfix.txt',
     'is not listed on Blocklist.de Postfix',
     'is listed on Blocklist.de Postfix',
     True),

    #Mirai Tracker (Mirai botnet IPs)
    ('https://mirai.security.gives/data/ip_list.txt',
     'is not listed on Mirai Tracker',
     'is listed on Mirai Tracker',
     True),

    #FireHOL Proxies (known proxies)
    ('https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_proxies.netset',
     'is not listed on FireHOL Proxies',
     'is listed on FireHOL Proxies',
     True),
    ]

def color(text, color_code):
    if sys.platform == "win32" and os.getenv("TERM") != "xterm":
        return text

    return '\x1b[%dm%s\x1b[0m' % (color_code, text)


def red(text):
    return color(text, 31)


def blink(text):
    return color(text, 5)


def green(text):
    return color(text, 32)


def blue(text):
    return color(text, 34)


def content_test(url, badip):
    import ipaddress
    try:
        html_content = cached_get_text(url)
        if isinstance(html_content, bytes):
            html_content = html_content.decode("utf-8", errors="replace")
        try:
            target_ip = ipaddress.ip_address(badip)
        except ValueError:
            return False
        for line in html_content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            # Strip inline comments and take first whitespace token
            # Handles tab-delimited formats (e.g. IPsum: "1.2.3.4\t3")
            line = line.split("#")[0].strip().split(";")[0].strip()
            if not line:
                continue
            line = line.split()[0]  # first token only
            if not line:
                continue
            try:
                if "/" in line:
                    # Quick first-octet filter before full CIDR check
                    if line.split(".")[0] == badip.split(".")[0]:
                        if target_ip in ipaddress.ip_network(line, strict=False):
                            return False  # listed
                else:
                    if ipaddress.ip_address(line) == target_ip:
                        return False  # listed
            except ValueError:
                continue
        return True  # not listed
    except:
        return False

def get_ip_bl(my_ip):
    output_html = ""
    listed_sources = []
    url_checks_html = "<h2 class='section-title'>URL Feed Checks</h2><table class='url-checks-table'><tr><th>Feed</th><th>URL</th><th>Entries</th><th>Status</th></tr>"
    active_bls = [bl for bl in bls if bl not in DISABLED_DNSBLS]



    badip = my_ip


    BAD = 0
    GOOD = 0
    total_feed_entries_checked = 0

    def check_url_blacklist(url_entry):
        url, succ, fail, mal = url_entry
        try:
            is_clean = cached_feed_check(url, badip)
            entry_count = cached_feed_entry_count(url)
            return is_clean, succ, fail, url, entry_count
        except Exception:
            return False, succ, fail, url, 0

    with ThreadPoolExecutor(max_workers=min(URL_CHECK_WORKERS, len(URLS))) as executor:
        for is_clean, succ, fail, url_value, entry_count in executor.map(check_url_blacklist, URLS):
            source_name = fail.replace('is listed on ', '').strip()
            status_class = "url-clear"
            status_label = "Not Listed"
            total_feed_entries_checked = total_feed_entries_checked + int(entry_count)

            if is_clean:
                (green('{0} {1}'.format(badip, succ)))
                GOOD = GOOD + 1
            else:
                (make_red_and_bold('{0} {1}'.format(badip, fail)))
                BAD = BAD + 1
                listed_sources.append(source_name)
                status_class = "url-hit"
                status_label = "LISTED"

            url_checks_html = url_checks_html + (
                "<tr class='" + status_class + "'><td>" + source_name + "</td><td>" + url_value + "</td><td>" + str(entry_count) + "</td><td><b>" + status_label + "</b></td></tr>"
            )

    url_checks_html = url_checks_html + "<tr><td colspan='4'><b>Total indicators checked across source URLs: " + str(total_feed_entries_checked) + "</b></td></tr>"
    url_checks_html = url_checks_html + "</table><br>"

    BAD = BAD
    GOOD = GOOD
    output_html = output_html + "<table class='center3'> <tr><td>"

    def check_dns_blacklist(bl):
        dnsbl_data = cached_dnsbl_status(badip, bl)
        if dnsbl_data["status"] == "listed":
            listed_html = (
                "<div style='background:#facc15;color:#1f2937;font-weight:bold;"
                "padding:6px 10px;border-radius:6px;margin:4px 0;'>"
                + badip + ' is listed in ' + bl
                + ' (%s: %s)</div>' % (dnsbl_data["answer"], dnsbl_data["txt"])
            )
            return {
                "listed": True,
                "good": 0,
                "bad": 1,
                "html": listed_html,
                "warning": None,
                "source": bl,
            }
        if dnsbl_data["status"] == "not listed":
            return {
                "listed": False,
                "good": 1,
                "bad": 0,
                "html": ('not listed in ' + bl) + "<br>",
                "warning": None,
                "source": bl,
            }
        if dnsbl_data["status"] == "timeout":
            return {
                "listed": False,
                "good": 0,
                "bad": 0,
                "html": "",
                "warning": blink('WARNING: Timeout querying ' + bl),
                "source": bl,
            }
        if dnsbl_data["status"] == "no nameservers":
            return {
                "listed": False,
                "good": 0,
                "bad": 0,
                "html": "",
                "warning": blink('WARNING: No nameservers for ' + bl),
                "source": bl,
            }
        if dnsbl_data["status"] == "no answer":
            return {
                "listed": False,
                "good": 0,
                "bad": 0,
                "html": "",
                "warning": blink('WARNING: No answer for ' + bl),
                "source": bl,
            }
        return {
            "listed": False,
            "good": 0,
            "bad": 0,
            "html": "",
            "warning": blink('WARNING: Error querying ' + bl),
            "source": bl,
        }

    with ThreadPoolExecutor(max_workers=max(1, min(32, len(active_bls)))) as executor:
        for result in executor.map(check_dns_blacklist, active_bls):
            output_html = output_html + result["html"]
            GOOD = GOOD + result["good"]
            BAD = BAD + result["bad"]
            if result["listed"]:
                listed_sources.append(result["source"])


    if listed_sources:
        listed_section = "<br><b>Listed on:</b><br>" + "<br>".join(sorted(set(listed_sources))) + "<br><br>"
        output_html = listed_section + url_checks_html + output_html
    else:
        output_html = url_checks_html + output_html


    output_html = output_html + "</td></tr></table'> "
    output_html = "<br> " +  (make_red_and_bold('\n{0} is on {1}/{2} blacklists.\n'.format(badip, BAD, (GOOD + BAD)))) + output_html

    return output_html


def make_red_and_bold(str_br):
    str_br= "<font style='color:red;'><b>" + str(str_br) + "</font></b>"
    return str_br

def get_ipq(ip):
    output_html = ""
    req = 'https://ipqualityscore.com/api/json/ip/al5mcqVmkspF3fAUUqxFywzs9sx1FeRk/' + str(ip) + '?strictness=0&allow_public_access_points=true&fast=true&lighter_penalties=true&mobile=true'

    data = cached_get_json(req)
    print(data["vpn"])
    fs = data["fraud_score"]
    if fs >= 60:
        data["fraud_score"] = "<span style='background:#dc2626;color:#fff;font-weight:bold;padding:2px 6px;border-radius:4px;'>" + str(fs) + "%</span>"
    elif fs >= 10:
        data["fraud_score"] = "<span style='background:#facc15;color:#1f2937;font-weight:bold;padding:2px 6px;border-radius:4px;'>" + str(fs) + "%</span>"
    else:
        data["fraud_score"] = str(fs) + "%"

    def yw(val, flag=True):
        """Wrap value in yellow highlight if it equals flag."""
        if val == flag:
            return "<span style='background:#facc15;color:#1f2937;font-weight:bold;padding:2px 6px;border-radius:4px;'>" + str(val) + "</span>"
        return str(val)

    output_html = output_html + "<table class='center3'> <tr>"
    output_html = output_html + "<tr><td>Fraud score</td><td>" + str(data["fraud_score"]) + " suspicious</td></tr>"
    output_html = output_html + "<tr><td>Country code</td><td>" + str(data["country_code"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Region</td><td>" + str(data["region"]) + "</td></tr>"
    output_html = output_html + "<tr><td>City</td><td>" + str(data["city"]) + "</td></tr>"
    output_html = output_html + "<tr><td>ISP</td><td>" + str(data["ISP"]) + "</td></tr>"
    output_html = output_html + "<tr><td>ASN</td><td>" + str(data["ASN"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Organization</td><td>" + str(data["organization"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Is crawler</td><td>" + yw(data["is_crawler"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Timezone</td><td>" + str(data["timezone"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Mobile</td><td>" + yw(data["mobile"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Proxy</td><td>" + yw(data["proxy"]) + "</td></tr>"
    output_html = output_html + "<tr><td>VPN</td><td>" + yw(data["vpn"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Tor</td><td>" + yw(data["tor"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Active vpn</td><td>" + yw(data["active_vpn"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Active tor</td><td>" + yw(data["active_tor"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Recent abuse</td><td>" + yw(data["recent_abuse"]) + "</td></tr>"
    output_html = output_html + "<tr><td>Bot status</td><td>" + yw(data["bot_status"]) + "</td></tr>"
    #output_html = output_html + "<tr><td >Latitude </td><td> " + str(float(data["latitude"])) + "</td></tr>"
    #output_html = output_html + "<tr><td >Longitude </td><td> " + str(float(data["longitude"])) + "</td></tr>"
    output_html = output_html + "</table>"
    print(data["longitude"])
    return output_html

def is_registered(domain_name):
    """
    A function that returns a boolean indicating
    whether a `domain_name` is registered
    """
    try:
        w = whois.whois(domain_name)
    except Exception:
        return False
    else:
        return bool(w.domain_name)

def output_domain_date_create(data):


    if isinstance(data, str):
        print(data)
        data = datetime.strptime(data, '%Y-%m-%dT%H:%M:%S')

    output = "<br>" + ("Domain creation date:" + " " + data.strftime("%m-%d-%Y"))
    today = datetime.now(tz=data.tzinfo) if data.tzinfo else datetime.now()
    diff = data - today
    diffdays = diff.days * (-1)
    if diffdays < 90:
        output = output + "<br><font style='color:red;'><b>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("Registered" + " " + str(diffdays) + " " + "Days ago </b></font>")
    else:
        output = output + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("Registered" + " " + str(diffdays) + " " + "Days ago")

    return output

def output_domain_date_exp(data):
    known_host = "unknown"
    known_registrar = "unknown"
    known_email = "unknown"
    known_ns = "unknown"


    if isinstance(data, str):
        print(data)
        data = datetime.datetime.strptime(data, '%Y-%m-%dT%H:%M:%S')

    output = "<br>" + ("Domain expiration date:" + " " + data.strftime("%m-%d-%Y"))
    today = datetime.datetime.now()
    diff = data - today
    diffdays = diff.days
    if diffdays < 90:
        output = output + "<br><font style='color:red;'><b>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("Expires in " + " " + str(diffdays) + " " + "Days </b></font>")
    else:
        output = output + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("Expires in" + " " + str(diffdays) + " " + "Days")

    return output


def lookup_asn_data(ip_addr):
    # Reuse internal /ip JSON data path instead of direct bgpview/ip-api calls.
    result = {
        "as_name": "unknown",
        "asn": "unknown",
        "prefix": "unknown",
        "cc": "unknown",
    }

    try:
        ip_info = get_ip_json(str(ip_addr), include_blacklists=False)
        ipq = ip_info.get("ipqualityscore") or {}

        org_value = ipq.get("organization")
        isp_value = ipq.get("isp")
        asn_value = ipq.get("asn")
        cc_value = ipq.get("country_code")

        if org_value:
            result["as_name"] = str(org_value)
        elif isp_value:
            result["as_name"] = str(isp_value)

        if asn_value:
            result["asn"] = str(asn_value)

        if cc_value:
            result["cc"] = str(cc_value)
    except Exception:
        pass

    return result


def parse_geoip_text(geoip_raw):
    parsed = {}
    raw_text = (geoip_raw or "").strip()
    if not raw_text:
        return parsed

    for line in raw_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower().replace(" ", "_")
        normalized_value = value.strip()
        if normalized_value in {"None", "N/A", ""}:
            parsed[normalized_key] = None
            continue
        if normalized_key in {"latitude", "longitude"}:
            try:
                parsed[normalized_key] = float(normalized_value)
                continue
            except ValueError:
                pass
        parsed[normalized_key] = normalized_value

    return parsed


def get_IP_info(ip):
    output_html = ""
    output_html = output_html + get_ipq(ip)
    output_html = output_html + "</td><td>"
    output_html = output_html + str(get_ip_bl(ip))

    head = ' <head> \
    <link rel="stylesheet"\
          href="https://fonts.googleapis.com/css?family=Tangerine">\
    <style>\
            :root {\
                --bg-top: #031b4e;\
                --bg-bottom: #00132f;\
                --panel: #0d2f66;\
                --panel-soft: #123d7f;\
                --panel-deep: #0a244f;\
                --border: #2a6fb8;\
                --text: #e7f1ff;\
                --accent: #5eb2ff;\
                --warn: #facc15;\
            } \
      body {\
                font-family: "Trebuchet MS", "Segoe UI", sans-serif;\
                background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom)); \
                color: var(--text); \
                margin: 0;\
                padding: 24px 10px;\
      } \
        td { \
                        font-family: "Trebuchet MS", "Segoe UI", sans-serif;\
                        border: 1px solid var(--border); \
            padding:20px; \
            width:20%; \
                        color: var(--text); \
                        background-color: var(--panel-deep); \
            border-radius:12px; \
                        vertical-align: top;\
                        text-align: left;\
        }\
                h1, h2, h3, b, label { color: var(--text); }\
                a { color: var(--accent); }\
                input[type="text"] {\
                        background: #09244a;\
                        color: var(--text);\
                        border: 1px solid var(--border);\
                        border-radius: 8px;\
                        padding: 8px 10px;\
                        min-width: 280px;\
                }\
                input[type="submit"] {\
                        background: var(--accent);\
                        color: #032147;\
                        border: none;\
                        border-radius: 8px;\
                        padding: 9px 16px;\
                        font-weight: 700;\
                        cursor: pointer;\
                }\
       .center {\
          margin-left: auto;\
          margin-right: auto; \
          padding:10px; \
                    color: var(--text); \
                    background-color: var(--panel); \
                    border: 1px solid var(--border); \
          border-radius:6px; \
        }\
    .center3 {\
             padding:0px; \
                         color: var(--text); \
                         background-color: var(--panel-deep); \
                         border: 1px solid var(--border); \
             border-radius:6px; \
           }\
        .center2 {\
                vertical-align: top;\
                text - align: left;\
              margin-left: auto;\
              margin-right: auto; \
              padding:40px; \
                            color: var(--text); \
                            background-color: var(--panel-soft); \
                            border: 1px solid var(--border);\
              border-radius:6px; \
            }\
                .section-title {\
                        color: var(--accent);\
                        margin-top: 8px;\
                        margin-bottom: 8px;\
                }\
                .url-checks-table {\
                        width: 100%;\
                        border-collapse: collapse;\
                        background-color: #0a274f;\
                        border: 1px solid var(--border);\
                        border-radius: 8px;\
                        overflow: hidden;\
                }\
                .url-checks-table th, .url-checks-table td {\
                        border: 1px solid #1f4e89;\
                        padding: 6px 8px;\
                        width: auto;\
                        background: transparent;\
                        color: var(--text);\
                        word-break: break-word;\
                        font-size: 0.8em;\
                        vertical-align: top;\
                }\
                .url-checks-table th {\
                        background: #124382;\
                        color: #dff0ff;\
                }\
                .url-clear {\
                        background: #0c305f;\
                }\
                .url-hit td {\
                        background: var(--warn);\
                        color: #1f2937;\
                }\
    </style>\
  </head>'
    form = '' \
            '<table  class="center2" width=100%><tr><td><center><b><h1> IP Extended Look Up</h1></b> ' \
           '<br><form action="/ip" method="get">  <label for="fname">IP:</label>  <input type="text" id="ip" name="ip"><br><br>  <input type="submit" value="Submit"></form>' \
           '</center></center>'
    header = "<b><h1>" + "" + ip.upper() + "</h1></b>"
    context = ""

    tool_output = "<table  class='center' width=99%><tr><td>" + header + context + output_html + "</td></tr></table></td></tr></table>"

    output_html = head + form  + tool_output + render_jobs_modal()

    return  output_html
def get_ip_json(ip, include_blacklists=True):
    """Collect all IP intelligence data and return as a dict for JSON output."""
    import ipaddress
    result = {"ip": ip}

    # FQDN + GeoIP
    result["fqdn"] = socket.getfqdn(ip)
    geoip_raw = cached_get_text('http://api.hackertarget.com/geoip/?q=' + ip)
    if isinstance(geoip_raw, bytes):
        geoip_raw = geoip_raw.decode("utf-8", errors="replace")
    result["geoip"] = parse_geoip_text(geoip_raw)

    # IPQualityScore (raw, no HTML)
    try:
        req = ('https://ipqualityscore.com/api/json/ip/al5mcqVmkspF3fAUUqxFywzs9sx1FeRk/'
               + ip + '?strictness=0&allow_public_access_points=true&fast=true&lighter_penalties=true&mobile=true')
        ipq = cached_get_json(req)
        result["ipqualityscore"] = {
            "fraud_score": ipq.get("fraud_score"),
            "country_code": ipq.get("country_code"),
            "region": ipq.get("region"),
            "city": ipq.get("city"),
            "isp": ipq.get("ISP"),
            "asn": ipq.get("ASN"),
            "organization": ipq.get("organization"),
            "is_crawler": ipq.get("is_crawler"),
            "timezone": ipq.get("timezone"),
            "mobile": ipq.get("mobile"),
            "proxy": ipq.get("proxy"),
            "vpn": ipq.get("vpn"),
            "tor": ipq.get("tor"),
            "active_vpn": ipq.get("active_vpn"),
            "active_tor": ipq.get("active_tor"),
            "recent_abuse": ipq.get("recent_abuse"),
            "bot_status": ipq.get("bot_status"),
        }
    except Exception as e:
        result["ipqualityscore"] = {"error": str(e)}

    # URL feed checks
    url_feed_results = {}
    url_feed_listed = []
    url_feed_entry_counts = {}
    url_feed_entries_total = 0

    def check_url_blacklist_json(url_entry):
        url, succ, fail, mal = url_entry
        source_name = fail.replace('is listed on ', '').strip()
        try:
            is_clean = cached_feed_check(url, ip)
            entry_count = cached_feed_entry_count(url)
            return source_name, is_clean, entry_count
        except Exception:
            return source_name, None, 0

    if include_blacklists:
        with ThreadPoolExecutor(max_workers=min(URL_CHECK_WORKERS, len(URLS))) as executor:
            for source_name, is_clean, entry_count in executor.map(check_url_blacklist_json, URLS):
                url_feed_entry_counts[source_name] = int(entry_count)
                url_feed_entries_total = url_feed_entries_total + int(entry_count)
                if is_clean is None:
                    url_feed_results[source_name] = "error"
                elif is_clean:
                    url_feed_results[source_name] = "not listed"
                else:
                    url_feed_results[source_name] = "listed"
                    url_feed_listed.append(source_name)

        result["url_feeds"] = url_feed_results
        result["url_feed_entry_counts"] = url_feed_entry_counts

        # DNSBL checks
        active_bls = [bl for bl in bls if bl not in DISABLED_DNSBLS]
        dnsbl_results = {}
        dnsbl_listed = []

        def check_dns_blacklist_json(bl):
            dnsbl_data = cached_dnsbl_status(ip, bl)
            return bl, dnsbl_data["status"], dnsbl_data["answer"], dnsbl_data["txt"]

        with ThreadPoolExecutor(max_workers=max(1, min(32, len(active_bls)))) as executor:
            for bl, status, answer, txt in executor.map(check_dns_blacklist_json, active_bls):
                if status == "listed":
                    dnsbl_results[bl] = {"status": "listed", "answer": answer, "txt": txt}
                    dnsbl_listed.append(bl)
                else:
                    dnsbl_results[bl] = {"status": status}

        result["dnsbl"] = dnsbl_results

        all_listed = sorted(set(url_feed_listed + dnsbl_listed))
        result["summary"] = {
            "url_feeds_checked": len(url_feed_results),
            "url_feed_entries_total": url_feed_entries_total,
            "url_feeds_listed": len(url_feed_listed),
            "dnsbl_checked": len(dnsbl_results),
            "dnsbl_listed": len(dnsbl_listed),
            "total_listed": len(all_listed),
            "listed_on": all_listed,
        }
    else:
        result["url_feeds"] = {}
        result["url_feed_entry_counts"] = {}
        result["dnsbl"] = {}
        result["summary"] = {
            "url_feeds_checked": 0,
            "url_feed_entries_total": 0,
            "url_feeds_listed": 0,
            "dnsbl_checked": 0,
            "dnsbl_listed": 0,
            "total_listed": 0,
            "listed_on": [],
        }

    return result


# Press the green button in the gutter to run the script.
@app.route('/ip')
def getIP():
    ip = request.args.get('ip')
    if ip is None:
        return render_lookup_form_html("IP Extended Look Up", "/ip", "IP", "ip")
    result_format = 'json' if request.args.get('type') == 'json' else 'html'
    job_id = create_job('ip', ip, result_format)
    worker = threading.Thread(target=run_ip_job, args=(job_id, ip, result_format), daemon=True)
    worker.start()

    if result_format == 'json':
        return jsonify({
            "jobid": job_id,
            "status": "queued",
            "check_url": "/check?jobid=" + job_id,
        })

    return (
        "<h1>IP job queued</h1>"
        "<br>Job ID: <b>" + job_id + "</b>"
        "<br><a href='/check?jobid=" + job_id + "'>Check job status</a>"
        + render_jobs_modal()
    )

def get_email_info(domain_name):
    import ipaddress
    known_host = "unknown"
    known_registrar = "unknown"
    known_email = "unknown"
    known_ns = "unknown"
    output_html = ""

    if True:
        log_email_progress(domain_name, "starting lookup")
        output_html = output_html + "<h1>" + "" + "Domain records" + "</h1>"
        whois_info = whois.whois(domain_name)
        log_email_progress(domain_name, "whois lookup complete")
        output_html = output_html + "<br>" + "Registrar:<font style='color:red;'><b>" + " " + str(whois_info.registrar) + "</b></font>"
        known_registrar = whois_info.registrar
        #print(type(whois_info.creation_date))
        # get the creation time
        if not whois_info.creation_date == None:
            if isinstance(whois_info.creation_date, list):
                for datetime_var in whois_info.creation_date:
                    output_html = output_html + output_domain_date_create(datetime_var)
                    break
            else:
                output_html = output_html + output_domain_date_create(whois_info.creation_date)


        output_html = output_html + "<br><br><b>" + "" + "Whois Abuse Emails:" + "</b>"
        if isinstance(whois_info.emails, list):
            for email in whois_info.emails:

                output_html = output_html + "<br><font style='color:red;'>"  + email + "</font>"
        else:
            output_html = output_html + "<br><font style='color:red;'>" + ("" + " " + whois_info.emails) + "</font>"

        known_name_servers = dict()
        known_name_servers["GoDaddy"] = ["domaincontrol.com","akam.net"]
        known_name_servers["Cloudflare"] = ["cloudflare.com",]
        known_name_servers["Google"] = ["google",]
        known_name_server = list()


        my_resolver = dns.resolver.Resolver()

        try:
            log_email_progress(domain_name, "resolving NS records")
            ns_answers = my_resolver.resolve(domain_name, "NS")
            resolved_ns = []
            for ans in ns_answers:
                ns_value = str(ans).rstrip(".")
                resolved_ns.append(ns_value)
                for owner in known_name_servers:
                    for ns_hint in known_name_servers[owner]:
                        if ns_hint.lower() in ns_value.lower() and owner not in known_name_server:
                            known_name_server.append(owner)
            if known_name_server:
                known_ns = " and ".join(known_name_server)
            elif resolved_ns:
                known_ns = ", ".join(resolved_ns)
            log_email_progress(domain_name, f"NS lookup complete ({known_ns})")
        except Exception:
            log_email_progress(domain_name, "NS lookup failed")
            pass

        try:
            log_email_progress(domain_name, "resolving TXT records")
            answer_txt = my_resolver.resolve(domain_name, "TXT")
            output_html = output_html + "<br><br><h1>" + "" + "TXT records" + "</h1>"
            allowed = list()
            for ans in answer_txt:
                txt_value = " ".join(part.decode("utf-8", errors="replace") for part in getattr(ans, "strings", []))
                if not txt_value:
                    txt_value = str(ans).replace('" "', '').replace('"', '')
                output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + txt_value
                if "spf" in txt_value.lower():
                    for inc in txt_value.split():
                        if ":" not in inc:
                            continue
                        key, value = inc.split(":", 1)
                        if key.startswith("ip"):
                            allowed.append(value)
                        if key == "include":
                            allowed.append(value)


                #else:
                    #output_html = output_html + "<br>" + ("txt records" + " " + str(ans))
            output_html = output_html + "<br><br><h1>" + "" + "SPF records" + "</h1>"
            output_html = output_html + "<b>" + ("Email Sources Allowed to send on behalf of" + " " + str(domain_name)) + "</b>"
            log_email_progress(domain_name, f"TXT lookup complete ({len(allowed)} SPF entries)")

            allowed = list(dict.fromkeys(allowed))
            spf_ip_targets = []
            for allow in allowed:
                try:
                    if "/" in allow:
                        network = ipaddress.ip_network(allow, strict=False)
                        if network.prefixlen == network.max_prefixlen:
                            spf_ip_targets.append((allow, str(network.network_address)))
                    else:
                        spf_ip_targets.append((allow, str(ipaddress.ip_address(allow))))
                except ValueError:
                    continue

            def spf_blacklist_status(allow):
                allow_label, candidate_ip = allow
                return allow_label, check_ip_blacklist_status(candidate_ip)

            with ThreadPoolExecutor(max_workers=min(20, max(1, len(spf_ip_targets)))) as executor:
                log_email_progress(domain_name, "checking SPF IP blacklist status")
                for allow, blacklist_status in executor.map(spf_blacklist_status, spf_ip_targets):
                    output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("" + " " + ip_to_link(allow))
                    if blacklist_status is True:
                        output_html = output_html + " <font style='color:red;'>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "" + "Blacklisted" + "</font>"
            log_email_progress(domain_name, "SPF processing complete")
        except:
            log_email_progress(domain_name, "TXT lookup failed")
            output_html = output_html + "<br><br><b>" + "" + "NO TXT records" + "</b>"



        #output_html = output_html + "<br><br><b>" + "" + "Hosting records" + "</b>"
        log_email_progress(domain_name, "resolving A records")
        answers = my_resolver.resolve(domain_name, "A")
        a_records = [str(ans) for ans in answers]
        log_email_progress(domain_name, "checking A record blacklist status")
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(a_records)))) as executor:
            a_blacklist_results = list(executor.map(check_ip_blacklist_status, a_records))
        output_html = output_html + "</td><td><h1>" + "" + "Hosting Information" + "</h1>"
        for ans, blacklist_status in zip(a_records, a_blacklist_results):
        #    h = CensysHosts()

            # Fetch a specific host and its services
        #    host = h.view(str(ans))
            #print(host)
            #print(host["location"])


            output_html = output_html + "<br><br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("A record: " + " " + ip_to_link(ans))
            # output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("Ports open:" + " " + str(len(host["services"])))
            # if len(host["services"]) < 10:
            #     for service in host["services"]:
            #
            #         output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("Port:" + " <a target=new href='https://" + domain_name + ":" + str(service["port"]) + "'>" + str(service["port"])) + "</a>"
            #
            #         output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + (
            #                     "Service Name:" + " " + str(service["service_name"]))
            #
            # if "location" in host:
            #     output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "" + " <a target=new href='http://www.google.com/maps/place/" + str(host["location"]["coordinates"]["latitude"]) + "," + str(host["location"]["coordinates"]["longitude"]) + "'> Map </a>"
            #
            #     if "country" in host["location"]:
            #         output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("Country:" + " " + host["location"]["country"])
            #     if "city" in host["location"]:
            #         output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("City:" + " " + host["location"]["city"])
            #     output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("Postal Code:" + " " + host["location"]["postal_code"])
            #     output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("Timezone:" + " " + host["location"]["timezone"])
            #     if "province" in host["location"]:
            #         output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("State:" + " " + host["location"]["province"])




            try:
                data = lookup_asn_data(str(ans))
                output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("ASN Name:<font style='color:red;'>" + " " + str(data["as_name"])) + "</font>"
                if str(data["as_name"]) != "unknown":
                    known_host = str(data["as_name"])
                elif known_host == "unknown":
                    known_host = str(ans)
                output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;&nbsp;" +  ("ASN#:" + " " + str(data["asn"]))
                output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;&nbsp;" +  ("Prefix:" + " " + str(data["prefix"]))
                output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;&nbsp;" +  ("Country Code:" + " " + str(data["cc"]))
            except:
                if known_host == "unknown":
                    known_host = str(ans)
                output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "" + "NO ASN records for host" + ""
            ###################################################################
            if blacklist_status is True:
                output_html = output_html + "<br><font style='color:red;'>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "" + "Blacklisted" + "</font>"
            else:
                output_html = output_html + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "" + "Not Blacklisted" + ""

            ####################################################################

        log_email_progress(domain_name, "A record processing complete")

        try:
            log_email_progress(domain_name, "resolving MX records")
            answers = my_resolver.resolve(domain_name, "MX")
            output_html = output_html + "<br><br><h1>"  + "" + "Email records"+ "</h1>"
            known_mx_servers = dict()
            known_mx_servers["Office 365"] = ["outlook.com",]
            known_mx_servers["Mimecast"] = ["mimecast.com", ]
            known_mx_servers["Proofpoint"] = ["proofpoint.com", ]
            known_mx_servers["Google"] = ["google", ]
            known_mx_servers["Sailthru"] = ["sailthru", ]

            known_mx_server = list()
            output_html_mx =""
            mx_records = []
            for ans in answers:
                for owner in known_mx_servers:
                    for mx in known_mx_servers[owner]:
                        if mx.lower() in str(ans).lower():
                            print(owner)
                            if owner not in known_mx_server:
                                known_mx_server.append(owner)
                emailIP = socket.gethostbyname(str(ans).split(" ")[1])
                mx_records.append((str(ans), str(emailIP)))
            log_email_progress(domain_name, "checking MX blacklist status")
            with ThreadPoolExecutor(max_workers=min(8, max(1, len(mx_records)))) as executor:
                mx_blacklist_results = list(executor.map(check_ip_blacklist_status, [email_ip for _, email_ip in mx_records]))
            for (ans, emailIP), blacklist_status in zip(mx_records, mx_blacklist_results):
                output_html_mx = output_html_mx + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("MX record:" + " " +  str(ans))
                output_html_mx = output_html_mx + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + ("MX record ip: " + " " + ip_to_link(emailIP))
                try:
                    data = lookup_asn_data(str(emailIP))
                    output_html_mx = output_html_mx + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;&nbsp;" + (
                                "ASN Name:<font style='color:red;'>" + " " + str(data["as_name"])) + "</font>"
                    if str(data["as_name"]) != "unknown":
                        known_host = str(data["as_name"])
                    elif known_host == "unknown":
                        known_host = str(emailIP)
                    output_html_mx = output_html_mx + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;&nbsp;" + (
                                "ASN#:" + " " + str(data["asn"]))
                    output_html_mx = output_html_mx + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;&nbsp;" + (
                                "Prefix:" + " " + str(data["prefix"]))
                    output_html_mx = output_html_mx + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;&nbsp;" + (
                                "Country Code:" + " " + str(data["cc"]))
                except:
                    if known_host == "unknown":
                        known_host = str(emailIP)
                    output_html_mx = output_html_mx + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "" + "NO ASN records for host" + ""
                ###################################################################
                if blacklist_status is True:
                    output_html_mx = output_html_mx + "<br><font style='color:red;'>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "" + "Blacklisted" + "</font>"
                else:
                    output_html_mx = output_html_mx + "<br>" + "&nbsp;&nbsp;&nbsp;&nbsp;" + "" + "Not Blacklisted" + ""

                ####################################################################
                #print()
                #print(str(ans))

                known_email = str(ans)

            for owner in known_mx_server:
                output_html = output_html + "<br><br><b>" + "" + "Known Email Protection System: <font style='color:red;'>" + owner + "</b></font>"
                known_email = owner
            if len(known_mx_server) > 1:
                known_email = " and ".join(known_mx_server)
            output_html = output_html + output_html_mx
            log_email_progress(domain_name, "MX processing complete")
        except:
            log_email_progress(domain_name, "MX lookup failed")
            output_html = output_html + "<br><br><b>" + "" + "NO MX records" + "</b>"

    log_email_progress(domain_name, "lookup complete")

    head = ' <head> \
    <link rel="stylesheet"\
          href="https://fonts.googleapis.com/css?family=Tangerine">\
    <style>\
            :root {\
                --bg-top: #031b4e;\
                --bg-bottom: #00132f;\
                --panel: #0d2f66;\
                --panel-soft: #123d7f;\
                --panel-deep: #0a244f;\
                --border: #2a6fb8;\
                --text: #e7f1ff;\
                --accent: #5eb2ff;\
            } \
      body {\
                font-family: "Trebuchet MS", "Segoe UI", sans-serif;\
                background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom)); \
                color: var(--text); \
                margin: 0;\
                padding: 24px 10px;\
      } \
        td { \
                        border: 1px solid var(--border); \
            padding:20px; \
            width:50%; \
                        color: var(--text);\
                        background-color: var(--panel-deep); \
            border-radius:12px; \
                        vertical-align: top;\
                        text-align: left;\
        }\
                h1, h2, h3, b, label { color: var(--text); }\
                input[type="text"] {\
                        background: #09244a;\
                        color: var(--text);\
                        border: 1px solid var(--border);\
                        border-radius: 8px;\
                        padding: 8px 10px;\
                        min-width: 280px;\
                }\
                input[type="submit"] {\
                        background: var(--accent);\
                        color: #032147;\
                        border: none;\
                        border-radius: 8px;\
                        padding: 9px 16px;\
                        font-weight: 700;\
                        cursor: pointer;\
                }\
       .center {\
          margin-left: auto;\
          margin-right: auto; \
          padding:10px; \
                    color: var(--text); \
                    background-color: var(--panel); \
                    border: 1px solid var(--border); \
          border-radius:6px; \
        }\
        .center2 {\
              margin-left: auto;\
              margin-right: auto; \
              padding:40px; \
                            color: var(--text); \
                            background-color: var(--panel-soft); \
                            border: 1px solid var(--border);\
              border-radius:6px; \
            }\
    </style>\
  </head>'
    form = '' \
            '<table  class="center2" width=100%><tr><td><center><b><h1> Email Extended Look Up</h1></b> ' \
           '<br><form action="/email" method="get">  <label for="fname">Email:</label>  <input type="text" id="email" name="email"><br><br>  <input type="submit" value="Submit"></form>' \
           '</center></center>'
    header = "<b><h1>" + "" + domain_name.upper() + "</h1></b>"
    hosting_display = ip_to_link(known_host)
    context = "The registrar of the domain is <font style='color:red;'>" + str(known_registrar) + "</font>." \
    "<br> The namservers are <font style='color:red;'>" + known_ns + "</font>." \
    "<br>The hosting provider is <font style='color:red;'>" + hosting_display + "</font>." \
    "<br>The email system is <font style='color:red;'>" + known_email + "</font>. <br> <br>"

    tool_output = "<table  class='center' width=80%><tr><td>" + header + context + output_html + "</td></tr></table></td></tr></table>"

    output_html = head + form  + tool_output + render_jobs_modal()

    return  output_html


def get_email_json(domain_name):
    import ipaddress

    result = {
        "domain": domain_name,
        "registrar": "unknown",
        "whois_emails": [],
        "name_servers": [],
        "name_server_provider": "unknown",
        "txt_records": [],
        "spf": {
            "entries": [],
            "ip_targets": [],
        },
        "a_records": [],
        "mx_records": [],
        "hosting_provider": "unknown",
        "email_system": "unknown",
    }

    known_name_servers = {
        "GoDaddy": ["domaincontrol.com", "akam.net"],
        "Cloudflare": ["cloudflare.com"],
        "Google": ["google"],
    }
    known_mx_servers = {
        "Office 365": ["outlook.com"],
        "Mimecast": ["mimecast.com"],
        "Proofpoint": ["proofpoint.com"],
        "Google": ["google"],
        "Sailthru": ["sailthru"],
    }

    known_host = "unknown"
    known_email = "unknown"

    try:
        whois_info = whois.whois(domain_name)
        result["registrar"] = str(whois_info.registrar) if whois_info.registrar else "unknown"

        if isinstance(whois_info.emails, list):
            result["whois_emails"] = [str(x) for x in whois_info.emails if x]
        elif whois_info.emails:
            result["whois_emails"] = [str(whois_info.emails)]
    except Exception as e:
        result["whois_error"] = str(e)

    my_resolver = dns.resolver.Resolver()

    try:
        ns_answers = my_resolver.resolve(domain_name, "NS")
        ns_records = [str(ans).rstrip(".") for ans in ns_answers]
        result["name_servers"] = ns_records

        matched_ns = []
        for ns_value in ns_records:
            for owner, hints in known_name_servers.items():
                for ns_hint in hints:
                    if ns_hint.lower() in ns_value.lower() and owner not in matched_ns:
                        matched_ns.append(owner)
        if matched_ns:
            result["name_server_provider"] = " and ".join(matched_ns)
    except Exception as e:
        result["ns_error"] = str(e)

    try:
        answer_txt = my_resolver.resolve(domain_name, "TXT")
        allowed = []
        for ans in answer_txt:
            txt_value = " ".join(part.decode("utf-8", errors="replace") for part in getattr(ans, "strings", []))
            if not txt_value:
                txt_value = str(ans).replace('" "', '').replace('"', '')
            result["txt_records"].append(txt_value)

            if "spf" in txt_value.lower():
                for inc in txt_value.split():
                    if ":" not in inc:
                        continue
                    key, value = inc.split(":", 1)
                    if key.startswith("ip") or key == "include":
                        allowed.append(value)

        allowed = list(dict.fromkeys(allowed))
        result["spf"]["entries"] = allowed

        spf_ip_targets = []
        for allow in allowed:
            try:
                if "/" in allow:
                    network = ipaddress.ip_network(allow, strict=False)
                    if network.prefixlen == network.max_prefixlen:
                        spf_ip_targets.append((allow, str(network.network_address)))
                else:
                    spf_ip_targets.append((allow, str(ipaddress.ip_address(allow))))
            except ValueError:
                continue

        def spf_blacklist_status(allow):
            allow_label, candidate_ip = allow
            return {
                "entry": allow_label,
                "ip": candidate_ip,
                "blacklisted": check_ip_blacklist_status(candidate_ip),
            }

        if spf_ip_targets:
            with ThreadPoolExecutor(max_workers=min(20, max(1, len(spf_ip_targets)))) as executor:
                result["spf"]["ip_targets"] = list(executor.map(spf_blacklist_status, spf_ip_targets))
    except Exception as e:
        result["txt_error"] = str(e)

    try:
        a_answers = my_resolver.resolve(domain_name, "A")
        a_records = [str(ans) for ans in a_answers]
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(a_records)))) as executor:
            a_blacklist_results = list(executor.map(check_ip_blacklist_status, a_records))

        for ip_value, blacklist_status in zip(a_records, a_blacklist_results):
            asn_data = lookup_asn_data(ip_value)
            if str(asn_data.get("as_name", "unknown")) != "unknown":
                known_host = str(asn_data.get("as_name"))
            elif known_host == "unknown":
                known_host = ip_value

            result["a_records"].append({
                "ip": ip_value,
                "blacklisted": blacklist_status,
                "asn": asn_data,
            })
    except Exception as e:
        result["a_error"] = str(e)

    try:
        mx_answers = my_resolver.resolve(domain_name, "MX")
        mx_records = []
        detected_mx = []

        for ans in mx_answers:
            mx_value = str(ans)
            for owner, hints in known_mx_servers.items():
                for mx_hint in hints:
                    if mx_hint.lower() in mx_value.lower() and owner not in detected_mx:
                        detected_mx.append(owner)
            email_ip = socket.gethostbyname(str(ans).split(" ")[1])
            mx_records.append((mx_value, email_ip))

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(mx_records)))) as executor:
            mx_blacklist_results = list(executor.map(check_ip_blacklist_status, [email_ip for _, email_ip in mx_records]))

        for (mx_value, email_ip), blacklist_status in zip(mx_records, mx_blacklist_results):
            asn_data = lookup_asn_data(email_ip)
            if str(asn_data.get("as_name", "unknown")) != "unknown":
                known_host = str(asn_data.get("as_name"))
            elif known_host == "unknown":
                known_host = email_ip

            result["mx_records"].append({
                "record": mx_value,
                "ip": email_ip,
                "blacklisted": blacklist_status,
                "asn": asn_data,
            })

        if detected_mx:
            known_email = " and ".join(detected_mx) if len(detected_mx) > 1 else detected_mx[0]
    except Exception as e:
        result["mx_error"] = str(e)

    result["hosting_provider"] = known_host
    result["email_system"] = known_email

    return result


def render_check_html(content_html, job_id_value=""):
    head = ' <head> \
    <link rel="stylesheet"\
    href="https://fonts.googleapis.com/css?family=Tangerine">\
    <style>\
        :root {\
        --bg-top: #031b4e;\
        --bg-bottom: #00132f;\
        --panel: #0d2f66;\
        --panel-soft: #123d7f;\
        --panel-deep: #0a244f;\
        --border: #2a6fb8;\
        --text: #e7f1ff;\
        --accent: #5eb2ff;\
        } \
    body {\
        font-family: "Trebuchet MS", "Segoe UI", sans-serif;\
        background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom)); \
        color: var(--text); \
        margin: 0;\
        padding: 24px 10px;\
    } \
    td { \
            border: 1px solid var(--border); \
        padding:20px; \
        width:50%; \
            color: var(--text);\
            background-color: var(--panel-deep); \
        border-radius:12px; \
            vertical-align: top;\
            text-align: left;\
    }\
        h1, h2, h3, b, label { color: var(--text); }\
        a { color: var(--accent); }\
        input[type="text"] {\
            background: #09244a;\
            color: var(--text);\
            border: 1px solid var(--border);\
            border-radius: 8px;\
            padding: 8px 10px;\
            min-width: 280px;\
        }\
        input[type="submit"] {\
            background: var(--accent);\
            color: #032147;\
            border: none;\
            border-radius: 8px;\
            padding: 9px 16px;\
            font-weight: 700;\
            cursor: pointer;\
        }\
     .center {\
    margin-left: auto;\
    margin-right: auto; \
    padding:10px; \
            color: var(--text); \
            background-color: var(--panel); \
            border: 1px solid var(--border); \
    border-radius:6px; \
    }\
    .center2 {\
        margin-left: auto;\
        margin-right: auto; \
        padding:40px; \
                color: var(--text); \
                background-color: var(--panel-soft); \
                border: 1px solid var(--border);\
        border-radius:6px; \
        }\
    .check-jobs-controls {display:flex;align-items:center;gap:10px;margin:10px 0 6px 0;}\
    .check-jobs-search {background:#09244a;color:var(--text);border:1px solid var(--border);border-radius:8px;padding:8px 10px;min-width:260px;}\
    .check-jobs-hint {font-size:0.9em;color:#cde4ff;}\
    .check-jobs-table {width: 100%; border-collapse: collapse; margin-top: 12px;}\
    .check-jobs-table th, .check-jobs-table td {border: 1px solid var(--border); padding: 8px; text-align: left;}\
    .check-jobs-table th {background: #124382; color: #dff0ff; cursor:pointer; user-select:none;}\
    .check-jobs-table th .sort-indicator {font-size:0.85em;opacity:0.8;padding-left:6px;}\
    .check-jobs-pager {display:flex;align-items:center;gap:10px;margin-top:10px;}\
    .check-jobs-pager button {background:var(--accent);color:#032147;border:none;border-radius:8px;padding:6px 10px;font-weight:700;cursor:pointer;}\
    .check-jobs-pager button[disabled] {opacity:0.45;cursor:not-allowed;}\
    .check-jobs-page-info {color:#dff0ff;font-size:0.9em;}\
    </style>\
</head>'
    form = '' \
       '<table  class="center2" width=100%><tr><td><center><b><h1> Job Status Check</h1></b> ' \
       '<br><form action="/check" method="get">  <label for="jobid">Job ID:</label>  <input type="text" id="jobid" name="jobid" value="' + html.escape(str(job_id_value or "")) + '"><br><br>  <input type="submit" value="Submit"></form>' \
       '</center></center>'
    tool_output = "<table  class='center' width=80%><tr><td>" + content_html + "</td></tr></table></td></tr></table>"
    return head + form + tool_output + render_jobs_modal()


def render_lookup_form_html(page_title, form_action, field_label, field_name, field_value=""):
    head = ' <head> \
    <link rel="stylesheet"\
        href="https://fonts.googleapis.com/css?family=Tangerine">\
    <style>\
        :root {\
        --bg-top: #031b4e;\
        --bg-bottom: #00132f;\
        --panel: #0d2f66;\
        --panel-soft: #123d7f;\
        --panel-deep: #0a244f;\
        --border: #2a6fb8;\
        --text: #e7f1ff;\
        --accent: #5eb2ff;\
        } \
        body {\
        font-family: "Trebuchet MS", "Segoe UI", sans-serif;\
        background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom)); \
        color: var(--text); \
        margin: 0;\
        padding: 24px 10px;\
        } \
    td { \
            border: 1px solid var(--border); \
        padding:20px; \
        width:50%; \
            color: var(--text);\
            background-color: var(--panel-deep); \
        border-radius:12px; \
            vertical-align: top;\
            text-align: left;\
    }\
        h1, h2, h3, b, label { color: var(--text); }\
        a { color: var(--accent); }\
        input[type="text"] {\
            background: #09244a;\
            color: var(--text);\
            border: 1px solid var(--border);\
            border-radius: 8px;\
            padding: 8px 10px;\
            min-width: 280px;\
        }\
        input[type="submit"] {\
            background: var(--accent);\
            color: #032147;\
            border: none;\
            border-radius: 8px;\
            padding: 9px 16px;\
            font-weight: 700;\
            cursor: pointer;\
        }\
         .center2 {\
            margin-left: auto;\
            margin-right: auto; \
            padding:40px; \
                color: var(--text); \
                background-color: var(--panel-soft); \
                border: 1px solid var(--border);\
            border-radius:6px; \
        }\
    </style>\
    </head>'
    form = '' \
           '<table  class="center2" width=100%><tr><td><center><b><h1>' + html.escape(str(page_title)) + '</h1></b> ' \
           '<br><form action="' + html.escape(str(form_action)) + '" method="get">  <label for="' + html.escape(str(field_name)) + '">' + html.escape(str(field_label)) + ':</label>  <input type="text" id="' + html.escape(str(field_name)) + '" name="' + html.escape(str(field_name)) + '" value="' + html.escape(str(field_value or "")) + '"><br><br>  <input type="submit" value="Submit"></form>' \
           '</center></center>'
    return head + form + render_jobs_modal()


def render_check_jobs_list(limit=30):
    rows = []
    for job in get_recent_jobs(limit):
        check_link = "/check?jobid=" + html.escape(str(job["job_id"]))
        if str(job.get("result_format")) == "json":
            check_link = check_link + "&type=json"
        created_text = datetime.fromtimestamp(job["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        rows.append(
            "<tr>"
            "<td>" + html.escape(str(job["job_id"])) + "</td>"
            "<td>" + html.escape(str(job["endpoint"])) + "</td>"
            "<td>" + html.escape(str(job["target"])) + "</td>"
            "<td>" + html.escape(str(job.get("result_format", ""))) + "</td>"
            "<td>" + html.escape(created_text) + "</td>"
            "<td>" + html.escape(str(job["status"])) + "</td>"
            "<td><a href='" + check_link + "'>Check</a></td>"
            "</tr>"
        )

    if not rows:
        return "<h2>Recent Jobs</h2><br>No jobs found."

    return (
        "<h2>Recent Jobs</h2>"
        "<div class='check-jobs-controls'>"
        "<input id='check-jobs-search' class='check-jobs-search' type='text' placeholder='Search jobs (id, endpoint, target, type, datetime, status)'>"
        "<span class='check-jobs-hint'>Click any column header to sort.</span>"
        "</div>"
        "<table id='check-jobs-table' class='check-jobs-table'>"
        "<thead><tr>"
        "<th data-col='0'>Job ID<span class='sort-indicator'></span></th>"
        "<th data-col='1'>Endpoint<span class='sort-indicator'></span></th>"
        "<th data-col='2'>Target<span class='sort-indicator'></span></th>"
        "<th data-col='3'>Type<span class='sort-indicator'></span></th>"
        "<th data-col='4'>Datetime<span class='sort-indicator'></span></th>"
        "<th data-col='5'>Status<span class='sort-indicator'></span></th>"
        "<th data-col='6'>Action<span class='sort-indicator'></span></th>"
        "</tr></thead>"
        "<tbody>"
        + "".join(rows) +
        "</tbody>"
        "</table>"
        "<div class='check-jobs-pager'>"
        "<button id='check-jobs-prev' type='button'>Prev</button>"
        "<span id='check-jobs-page-info' class='check-jobs-page-info'></span>"
        "<button id='check-jobs-next' type='button'>Next</button>"
        "</div>"
        "<script>"
        "(function(){"
        "var table=document.getElementById('check-jobs-table');"
        "var searchInput=document.getElementById('check-jobs-search');"
        "var prevBtn=document.getElementById('check-jobs-prev');"
        "var nextBtn=document.getElementById('check-jobs-next');"
        "var pageInfo=document.getElementById('check-jobs-page-info');"
        "if(!table){return;}"
        "var tbody=table.querySelector('tbody');"
        "var headers=table.querySelectorAll('th[data-col]');"
        "var sortState={col:-1,asc:true};"
        "var pageSize=10;"
        "var currentPage=1;"
        "var filteredRows=[];"
        "function normalize(v){return String(v||'').toLowerCase();}"
        "function renderPage(){"
        "var trs=Array.prototype.slice.call(tbody.querySelectorAll('tr'));"
        "trs.forEach(function(tr){tr.style.display='none';});"
        "var total=filteredRows.length;"
        "var totalPages=Math.max(1,Math.ceil(total/pageSize));"
        "if(currentPage>totalPages){currentPage=totalPages;}"
        "var start=(currentPage-1)*pageSize;"
        "var end=Math.min(start+pageSize,total);"
        "for(var i=start;i<end;i++){filteredRows[i].style.display='';}"
        "if(pageInfo){pageInfo.textContent='Page '+currentPage+' of '+totalPages+' ('+total+' results)';}"
        "if(prevBtn){prevBtn.disabled=currentPage<=1;}"
        "if(nextBtn){nextBtn.disabled=currentPage>=totalPages;}"
        "}"
        "function applySearch(){"
        "var term=normalize(searchInput?searchInput.value:'');"
        "var trs=tbody.querySelectorAll('tr');"
        "filteredRows=[];"
        "trs.forEach(function(tr){"
        "var text=normalize(tr.textContent);"
        "if(term===''||text.indexOf(term)!==-1){filteredRows.push(tr);}"
        "});"
        "currentPage=1;"
        "renderPage();"
        "}"
        "function updateIndicators(){"
        "headers.forEach(function(h){"
        "var i=h.querySelector('.sort-indicator');"
        "if(!i){return;}"
        "var col=Number(h.getAttribute('data-col'));"
        "if(col===sortState.col){i.textContent=sortState.asc?'▲':'▼';}"
        "else{i.textContent='';}"
        "});"
        "}"
        "function sortByColumn(col){"
        "var allRows=Array.prototype.slice.call(tbody.querySelectorAll('tr'));"
        "sortState.asc=(sortState.col===col)?!sortState.asc:true;"
        "sortState.col=col;"
        "allRows.sort(function(a,b){"
        "var av=normalize(a.children[col]?a.children[col].textContent:'');"
        "var bv=normalize(b.children[col]?b.children[col].textContent:'');"
        "if(av<bv){return sortState.asc?-1:1;}"
        "if(av>bv){return sortState.asc?1:-1;}"
        "return 0;"
        "});"
        "allRows.forEach(function(r){tbody.appendChild(r);});"
        "updateIndicators();"
        "applySearch();"
        "}"
        "headers.forEach(function(h){"
        "h.addEventListener('click',function(){sortByColumn(Number(h.getAttribute('data-col')));});"
        "});"
        "if(searchInput){searchInput.addEventListener('input',applySearch);}"
        "if(prevBtn){prevBtn.addEventListener('click',function(){if(currentPage>1){currentPage--;renderPage();}});}"
        "if(nextBtn){nextBtn.addEventListener('click',function(){if(currentPage*pageSize<filteredRows.length){currentPage++;renderPage();}});}"
        "applySearch();"
        "})();"
        "</script>"
    )


# Press the green button in the gutter to run the script.
@app.route('/email')
def email():
    def normalize_domain_input(value):
        raw_value = str(value or "").strip()
        if not raw_value:
            return ""
        if "@" in raw_value:
            raw_value = raw_value.rsplit("@", 1)[1]
        if "://" in raw_value:
            parsed = urlparse(raw_value)
            raw_value = parsed.netloc or parsed.path
        raw_value = raw_value.split("/", 1)[0]
        raw_value = raw_value.split(":", 1)[0]
        return raw_value.strip().strip(".")

    if request.args.get('email') == None:
        return render_lookup_form_html("Email Extended Look Up", "/email", "Email", "email")
    else:
        domain_name = normalize_domain_input(request.args.get("email"))
        if not domain_name:
            return render_lookup_form_html("Email Extended Look Up", "/email", "Email", "email")
        result_format = 'json' if request.args.get('type') == 'json' else 'html'
        job_id = create_job('email', domain_name, result_format)
        worker = threading.Thread(target=run_email_job, args=(job_id, domain_name, result_format), daemon=True)
        worker.start()

        if result_format == 'json':
            return jsonify({
                "jobid": job_id,
                "status": "queued",
                "check_url": "/check?jobid=" + job_id,
            })

        return (
            "<h1>Email job queued</h1>"
            "<br>Job ID: <b>" + job_id + "</b>"
            "<br><a href='/check?jobid=" + job_id + "'>Check job status</a>"
            + render_jobs_modal()
        )


@app.route('/check')
def check_job():
    job_id = request.args.get('jobid')
    response_type = request.args.get('type')
    status_only = request.args.get('status_only') == '1'

    if not job_id:
        if response_type == 'json':
            return jsonify({"error": "missing jobid"}), 400
        return render_check_html(
            "<h1>Missing jobid</h1><br>Provide a job ID to look up status or retrieve a completed result."
            + render_check_jobs_list()
        ), 400

    job = get_job(job_id)
    if not job:
        if response_type == 'json':
            return jsonify({"error": "job not found", "jobid": job_id}), 404
        return render_check_html("<h1>Job not found</h1><br>Job ID: <b>" + html.escape(str(job_id)) + "</b>", job_id), 404

    status_payload = {
        "jobid": job["job_id"],
        "endpoint": job["endpoint"],
        "target": job["target"],
        "status": job["status"],
        "error": job["error_message"],
        "result_format": job["result_format"],
    }

    if status_only:
        return jsonify(status_payload)

    if job["status"] != "done":
        if response_type == 'json' or job["result_format"] == 'json':
            return jsonify(status_payload)
        return render_check_html(
            "<h1>Job status</h1>"
            "<br>Job ID: <b>" + job["job_id"] + "</b>"
            "<br>Status: <b>" + job["status"] + "</b>"
            + ("<br>Error: <b>" + str(job["error_message"]) + "</b>" if job["error_message"] else "")
        , job["job_id"])

    if job["result_format"] == 'json':
        try:
            return jsonify(json.loads(job["result_payload"] or "{}"))
        except Exception:
            return jsonify({"error": "invalid stored JSON payload", "jobid": job["job_id"]}), 500

    return job["result_payload"] or ""




init_job_db()
app.run(ssl_context='adhoc', host='0.0.0.0', port=1444)


