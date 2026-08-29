#!/usr/bin/env python3
"""Manual Windows-side Xray Stats API collector.

It executes a read-only remote statsquery over the existing SSH aliases and
posts hashed runtime identities plus counters to the Control Plane. It never
transfers UUIDs, passwords, tokens or full subscription URIs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


REMOTE_SCRIPT = r'''
import hashlib,json,os,subprocess
def h(v):
    return hashlib.sha256(str(v).strip().encode()).hexdigest()
boot_path='/proc/sys/kernel/random/boot_id'
boot=open(boot_path).read().strip() if os.path.exists(boot_path) else 'unknown'
for exe in ['/usr/local/x-ui/bin/xray-linux-amd64','/usr/local/bin/xray','/usr/bin/xray']:
    if not os.path.exists(exe):
        continue
    p=subprocess.run([exe,'api','statsquery','-server=127.0.0.1:62789','-pattern','user>>>'],capture_output=True,text=True,timeout=20)
    if p.returncode != 0:
        print(json.dumps({'ok':False,'error':'statsquery_failed','detail':p.stderr[:200]}))
        raise SystemExit(0)
    obj=json.loads(p.stdout)
    totals={}
    for item in obj.get('stat',[]):
        parts=item.get('name','').split('>>>')
        if len(parts) < 3: continue
        ref=parts[1]
        metric=parts[-1]
        if metric not in ('uplink','downlink'): continue
        row=totals.setdefault(h(ref),{'runtime_ref_hash':h(ref),'uplink_bytes':0,'downlink_bytes':0})
        row[metric+'_bytes']=int(item.get('value',0))
    print(json.dumps({'ok':True,'counter_epoch':h(boot),'observations':list(totals.values())},separators=(',',':')))
    raise SystemExit(0)
print(json.dumps({'ok':False,'error':'xray_binary_not_found'}))
'''


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def remote_stats(ssh_host: str) -> dict:
    encoded = base64.b64encode(REMOTE_SCRIPT.encode()).decode("ascii")
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", ssh_host, "base64 -d | sudo python3 -"],
        input=encoded.encode(), capture_output=True, timeout=45, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH/query failed for {ssh_host}: exit={result.returncode}")
    lines = [line for line in result.stdout.decode(errors="replace").splitlines() if line.strip().startswith("{")]
    if not lines:
        raise RuntimeError(f"no redacted stats result from {ssh_host}")
    return json.loads(lines[-1])


def post_json(endpoint: str, admin_token: str, body: dict) -> dict:
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/api/admin/ingest/observations",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def post_coverage(endpoint: str, admin_token: str, node_id: str, status: str, detail: str) -> dict:
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/api/admin/coverage",
        data=json.dumps({"node_id": node_id, "source": "xray-stats-api", "status": status,
                         "detail": detail, "observed_at": utc_now()}).encode(),
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--endpoint", help="Override the Control Plane endpoint for an SSH-tunnelled run")
    parser.add_argument("--admin-token-env", default="SPARKLINK_ADMIN_TOKEN")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    endpoint = args.endpoint or config["endpoint"]
    admin_token = os.environ.get(args.admin_token_env, "")
    if not admin_token:
        raise SystemExit(f"missing {args.admin_token_env}; keep the token outside Git")
    for node in config["nodes"]:
        node_id = node["node_id"]
        try:
            result = remote_stats(node["ssh_host"])
            if not result.get("ok"):
                print(json.dumps({"node_id": node_id, "status": "gap", "reason": result.get("error")}))
                continue
            if not result["observations"]:
                post_coverage(endpoint, admin_token, node_id, "unknown",
                              "StatsService reachable but no per-user counters returned; usage is not treated as zero")
                print(json.dumps({"node_id": node_id, "status": "unknown", "reason": "no_per_user_counters"}))
                continue
            payload = {
                "node_id": node_id,
                "source": "xray-stats-api",
                "counter_epoch": result["counter_epoch"],
                "observed_at": utc_now(),
                "observations": result["observations"],
            }
            response = post_json(endpoint, admin_token, payload)
            print(json.dumps({"node_id": node_id, "status": "ingested", **response}))
        except Exception as exc:
            print(json.dumps({"node_id": node_id, "status": "gap", "reason": str(exc)[:200]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
