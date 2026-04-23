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

## All Checked Lists

The IP workflow checks two major list groups:

1. URL/IP feed lists (downloaded and scanned for exact IP/CIDR matches)
2. DNSBL lists (DNS-based blacklist lookups)

### URL/IP Feed Lists

- EmergingThreats: http://rules.emergingthreats.net/blockrules/compromised-ips.txt
- AlienVault: http://reputation.alienvault.com/reputation.data
- BlocklistDE Bruteforce Login: http://www.blocklist.de/lists/bruteforcelogin.txt
- Feodo (same source URL as EmergingThreats entry in code): http://rules.emergingthreats.net/blockrules/compromised-ips.txt
- Abuse.ch Feodo Tracker: https://feodotracker.abuse.ch/downloads/ipblocklist.txt
- Abuse.ch SSLBL: https://sslbl.abuse.ch/blacklist/sslipblacklist.txt
- CINS Army: https://cinsscore.com/list/ci-badguys.txt
- Spamhaus DROP: https://www.spamhaus.org/drop/drop.txt
- Spamhaus EDROP: https://www.spamhaus.org/drop/edrop.txt
- FireHOL Level 1: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset
- Emerging Threats botcc: https://rules.emergingthreats.net/fwrules/emerging-Block-IPs.txt
- Greensnow: https://blocklist.greensnow.co/greensnow.txt
- FireHOL Level 2: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level2.netset
- FireHOL Level 3: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level3.netset
- FireHOL Level 4: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level4.netset
- FireHOL Abusers 1d: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_abusers_1d.netset
- FireHOL Abusers 30d: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_abusers_30d.netset
- FireHOL Anonymous: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_anonymous.netset
- FireHOL Webclient: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_webclient.netset
- Tor Exit Nodes: https://check.torproject.org/torbulkexitlist
- Blocklist.de SSH: http://www.blocklist.de/lists/ssh.txt
- Blocklist.de SMTP: http://www.blocklist.de/lists/mail.txt
- IPsum: https://raw.githubusercontent.com/stamparm/ipsum/master/ipsum.txt
- Abuse.ch Feodo Tracker Aggressive: https://feodotracker.abuse.ch/downloads/ipblocklist_aggressive.txt
- Team Cymru Full Bogons: https://www.team-cymru.org/Services/Bogons/fullbogons-ipv4.txt
- Blocklist.de All: http://www.blocklist.de/lists/all.txt
- Blocklist.de Apache: http://www.blocklist.de/lists/apache.txt
- Blocklist.de Bots: http://www.blocklist.de/lists/bots.txt
- Blocklist.de SIP: http://www.blocklist.de/lists/sip.txt
- Blocklist.de StrongIPs: http://www.blocklist.de/lists/strongips.txt
- Binary Defense banlist: https://www.binarydefense.com/banlist.txt
- StopForumSpam Toxic CIDR: https://www.stopforumspam.com/downloads/toxic_ip_cidr.txt
- Dan.me.uk Tor list: https://www.dan.me.uk/torlist/
- VoIPBL: http://www.voipbl.org/update/
- DShield 1d: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/dshield_1d.netset
- Etnetera Aggressive: https://security.etnetera.cz/feeds/etn_aggressive.txt
- Blocklist.de Postfix: http://www.blocklist.de/lists/postfix.txt
- Mirai Tracker: https://mirai.security.gives/data/ip_list.txt
- FireHOL Proxies: https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_proxies.netset

### DNSBL Lists

Configured DNSBLs:

- b.barracudacentral.org
- bl.spamcop.net
- blacklist.woody.ch
- cbl.abuseat.org
- combined.abuse.ch
- combined.rbl.msrbl.net
- db.wpbl.info
- dnsbl.cyberlogic.net
- dnsbl.sorbs.net
- drone.abuse.ch
- duinv.aupads.org
- dul.dnsbl.sorbs.net
- dul.ru
- dynip.rothen.com
- http.dnsbl.sorbs.net
- images.rbl.msrbl.net
- ips.backscatterer.org
- ix.dnsbl.manitu.net
- korea.services.net
- misc.dnsbl.sorbs.net
- noptr.spamrats.com
- ohps.dnsbl.net.au
- omrs.dnsbl.net.au
- osps.dnsbl.net.au
- osrs.dnsbl.net.au
- owfs.dnsbl.net.au
- pbl.spamhaus.org
- phishing.rbl.msrbl.net
- probes.dnsbl.net.au
- proxy.bl.gweep.ca
- rbl.interserver.net
- rdts.dnsbl.net.au
- relays.bl.gweep.ca
- relays.nether.net
- residential.block.transip.nl
- ricn.dnsbl.net.au
- rmst.dnsbl.net.au
- smtp.dnsbl.sorbs.net
- socks.dnsbl.sorbs.net
- spam.abuse.ch
- spam.dnsbl.sorbs.net
- spam.rbl.msrbl.net
- spam.spamrats.com
- spamrbl.imp.ch
- t3direct.dnsbl.net.au
- tor.dnsbl.sectoor.de
- torserver.tor.dnsbl.sectoor.de
- ubl.lashback.com
- ubl.unsubscore.com
- virus.rbl.jp
- virus.rbl.msrbl.net
- web.dnsbl.sorbs.net
- wormrbl.imp.ch
- xbl.spamhaus.org
- zen.spamhaus.org
- zombie.dnsbl.sorbs.net

Disabled DNSBLs (configured but skipped by default):

- db.wpbl.info
- ix.dnsbl.manitu.net
- rmst.dnsbl.net.au
- tor.dnsbl.sectoor.de
- torserver.tor.dnsbl.sectoor.de
