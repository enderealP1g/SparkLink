#!/usr/bin/env python3
"""Read-only Xray Stats API collector.

The default mode performs one collection cycle for manual fallback. Supplying
``--interval-seconds`` turns it into a long-running management-plane service.
SSH keys and the Control Plane admin token remain in protected runtime
locations; this module never transfers UUIDs, passwords, tokens or full URIs.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import ctypes
import hashlib
import json
import os
import signal
import socket
import subprocess
import urllib.error
import urllib.request
import threading
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path


REMOTE_SCRIPT = r'''
import hashlib,json,os,subprocess
from datetime import datetime,timezone

def h(v):
    return hashlib.sha256(str(v).strip().encode()).hexdigest()

def process_epoch():
    boot_path='/proc/sys/kernel/random/boot_id'
    boot=open(boot_path).read().strip() if os.path.exists(boot_path) else 'unknown'
    pid_result=subprocess.run(
        ['systemctl','show','-p','MainPID','--value','xray'],
        capture_output=True,text=True,timeout=10,
    )
    pid=pid_result.stdout.strip() if pid_result.returncode == 0 else 'unknown'
    if not pid.isdigit() or pid == '0':
        for candidate in sorted(os.listdir('/proc')):
            if not candidate.isdigit():
                continue
            try:
                raw_cmdline=open('/proc/'+candidate+'/cmdline','rb').read().split(b'\0')
                executable=os.path.basename(raw_cmdline[0].decode(errors='replace')) if raw_cmdline else ''
            except OSError:
                continue
            if executable.startswith('xray'):
                pid=candidate
                break
    start='unknown'
    if pid.isdigit() and pid != '0' and os.path.exists('/proc/'+pid+'/stat'):
        raw=open('/proc/'+pid+'/stat').read()
        fields=raw.rsplit(')',1)[1].strip().split()
        if len(fields) >= 20:
            start=fields[19]
    if boot == 'unknown' or pid in ('unknown', '0') or start == 'unknown':
        return None
    return h(boot+'|'+pid+'|'+start)

epoch_before=process_epoch()
if not epoch_before:
    print(json.dumps({'ok':False,'error':'counter_epoch_unavailable'},separators=(',',':')))
    raise SystemExit(0)
for exe in ['/usr/local/x-ui/bin/xray-linux-amd64','/usr/local/bin/xray','/usr/bin/xray']:
    if not os.path.exists(exe):
        continue
    p=subprocess.run(
        [exe,'api','statsquery','-server=127.0.0.1:62789','-pattern','user>>>'],
        capture_output=True,text=True,timeout=20,
    )
    if p.returncode != 0:
        print(json.dumps({'ok':False,'error':'statsquery_failed'},separators=(',',':')))
        raise SystemExit(0)
    try:
        obj=json.loads(p.stdout)
    except Exception:
        print(json.dumps({'ok':False,'error':'stats_response_invalid'},separators=(',',':')))
        raise SystemExit(0)
    if not isinstance(obj,dict) or not isinstance(obj.get('stat',[]),list):
        print(json.dumps({'ok':False,'error':'stats_response_invalid'},separators=(',',':')))
        raise SystemExit(0)
    epoch_after=process_epoch()
    if not epoch_after or epoch_before != epoch_after:
        print(json.dumps({'ok':False,'error':'counter_epoch_changed_during_query'},separators=(',',':')))
        raise SystemExit(0)
    metrics={}
    for item in obj.get('stat',[]):
        if not isinstance(item,dict) or not isinstance(item.get('name'),str):
            print(json.dumps({'ok':False,'error':'stats_shape_invalid'},separators=(',',':')))
            raise SystemExit(0)
        parts=item['name'].split('>>>')
        if len(parts) != 4 or parts[0] != 'user' or parts[2] != 'traffic' or not parts[1]:
            print(json.dumps({'ok':False,'error':'stats_shape_invalid'},separators=(',',':')))
            raise SystemExit(0)
        ref=parts[1]
        metric=parts[3]
        if metric not in ('uplink','downlink') or ref in metrics and metric in metrics[ref]:
            print(json.dumps({'ok':False,'error':'stats_shape_invalid'},separators=(',',':')))
            raise SystemExit(0)
        value=item.get('value')
        if isinstance(value,bool) or not isinstance(value,int):
            print(json.dumps({'ok':False,'error':'stats_value_invalid'},separators=(',',':')))
            raise SystemExit(0)
        if value < 0:
            print(json.dumps({'ok':False,'error':'stats_value_invalid'},separators=(',',':')))
            raise SystemExit(0)
        metrics.setdefault(ref,{})[metric]=value
    if any(set(values) != {'uplink','downlink'} for values in metrics.values()):
        print(json.dumps({'ok':False,'error':'partial_per_user_counters'},separators=(',',':')))
        raise SystemExit(0)
    totals=[{'runtime_ref_hash':h(ref),'uplink_bytes':values['uplink'],'downlink_bytes':values['downlink']}
            for ref,values in metrics.items()]
    now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    print(json.dumps({'ok':True,'counter_epoch':epoch_after,'observed_at':now,
                      'observations':totals},separators=(',',':')))
    raise SystemExit(0)
print(json.dumps({'ok':False,'error':'xray_binary_not_found'},separators=(',',':')))
'''


class CollectorError(RuntimeError):
    """A safe, non-secret collector failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def read_dpapi_token(path: Path) -> str:
    """Read a Windows LocalMachine DPAPI-protected, Base64-encoded token."""
    if os.name != "nt":
        raise CollectorError("dpapi_secret_requires_windows")
    try:
        encoded = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CollectorError("protected_secret_unreadable") from exc
    except UnicodeError as exc:
        raise CollectorError("protected_secret_encoding_invalid") from exc
    try:
        protected = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CollectorError("protected_secret_invalid") from exc
    if not protected:
        raise CollectorError("protected_secret_empty")

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    input_buffer = ctypes.create_string_buffer(protected)
    input_blob = DataBlob(len(protected), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = DataBlob()
    if not crypt32.CryptUnprotectData(ctypes.byref(input_blob), None, None, None, None, 0,
                                      ctypes.byref(output_blob)):
        raise CollectorError("dpapi_unprotect_failed")
    try:
        plain = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return plain.decode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise CollectorError("protected_secret_plaintext_invalid") from exc
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)


@contextlib.contextmanager
def control_plane_tunnel(ssh_host: str | None, forward_port: int):
    """Expose QQG loopback Control Plane only to this local collector process."""
    if not ssh_host:
        yield None
        return
    if not 1024 <= forward_port <= 65535:
        raise CollectorError("invalid_control_plane_forward_port")
    try:
        with socket.create_connection(("127.0.0.1", forward_port), timeout=0.2):
            raise CollectorError("control_plane_forward_port_in_use")
    except OSError:
        pass
    try:
        tunnel = subprocess.Popen(
            ["ssh", "-N", "-T", "-o", "BatchMode=yes",
             "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
             "-o", "ServerAliveCountMax=3", "-L",
             f"127.0.0.1:{forward_port}:127.0.0.1:8080", ssh_host],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise CollectorError("ssh_tunnel_start_failed") from exc
    try:
        for _ in range(40):
            if tunnel.poll() is not None:
                raise CollectorError("ssh_tunnel_exited_before_ready")
            try:
                with socket.create_connection(("127.0.0.1", forward_port), timeout=0.3):
                    break
            except OSError:
                pass
        else:
            raise CollectorError("ssh_tunnel_not_ready")
        yield f"http://127.0.0.1:{forward_port}"
    finally:
        if tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait(timeout=5)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def remote_stats(ssh_host: str) -> dict:
    encoded = base64.b64encode(REMOTE_SCRIPT.encode()).decode("ascii")
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", ssh_host,
         "base64 -d | sudo python3 -"],
        input=encoded.encode(), capture_output=True, timeout=45, check=False,
    )
    if result.returncode != 0:
        raise CollectorError("ssh_or_remote_query_failed")
    lines = [line for line in result.stdout.decode(errors="replace").splitlines()
             if line.strip().startswith("{")]
    if not lines:
        raise CollectorError("no_redacted_stats_result")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise CollectorError("collector_result_invalid") from exc


def _request_json(endpoint: str, admin_token: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        endpoint.rstrip("/") + path,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise CollectorError(f"control_plane_http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CollectorError("control_plane_unreachable") from exc


def post_json(endpoint: str, admin_token: str, body: dict) -> dict:
    return _request_json(endpoint, admin_token, "/api/admin/ingest/observations", body)


def post_coverage(endpoint: str, admin_token: str, node_id: str, status: str,
                  detail: str, source: str = "xray-stats-api") -> dict:
    return _request_json(
        endpoint, admin_token, "/api/admin/coverage",
        {"node_id": node_id, "source": source, "status": status,
         "detail": detail[:500], "observed_at": utc_now()},
    )


def post_heartbeat(endpoint: str, admin_token: str, summary: dict) -> dict:
    failed = int(summary.get("failed", 0))
    unknown = int(summary.get("unknown", 0))
    status = "failed" if failed and not summary.get("ingested") else ("degraded" if failed or unknown else "completed")
    return _request_json(
        endpoint, admin_token, "/api/admin/collector-heartbeat",
        {
            "collector_id": "windows-xray-stats-collector",
            "status": status,
            "attempted_nodes": int(summary.get("attempted", 0)),
            "ingested_nodes": int(summary.get("ingested", 0)),
            "failed_nodes": failed,
            "source": "windows-task-scheduler",
            "detail": f"unknown={unknown}",
            "observed_at": utc_now(),
        },
    )


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, CollectorError):
        return exc.code
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "invalid_collector_configuration"
    return exc.__class__.__name__.lower()


def validate_remote_observations(observations: object) -> list[dict]:
    """Reject partial or ambiguous counter rows before any ingest call."""
    if not isinstance(observations, list):
        raise CollectorError("remote_observations_invalid")
    normalized: list[dict] = []
    seen: set[str] = set()
    for item in observations:
        if not isinstance(item, dict):
            raise CollectorError("remote_observation_invalid")
        required = {"runtime_ref_hash", "uplink_bytes", "downlink_bytes"}
        if not required.issubset(item):
            raise CollectorError("partial_per_user_counters")
        runtime_hash = str(item["runtime_ref_hash"]).lower()
        if (len(runtime_hash) != 64
                or any(c not in "0123456789abcdef" for c in runtime_hash)):
            raise CollectorError("remote_runtime_ref_invalid")
        if runtime_hash in seen:
            raise CollectorError("duplicate_runtime_ref")
        seen.add(runtime_hash)
        uplink = item["uplink_bytes"]
        downlink = item["downlink_bytes"]
        if (isinstance(uplink, bool) or not isinstance(uplink, int)
                or isinstance(downlink, bool) or not isinstance(downlink, int)):
            raise CollectorError("remote_counter_invalid")
        if uplink < 0 or downlink < 0:
            raise CollectorError("remote_counter_invalid")
        normalized.append({
            "runtime_ref_hash": runtime_hash,
            "uplink_bytes": uplink,
            "downlink_bytes": downlink,
        })
    return normalized


def collect_node(endpoint: str, admin_token: str, node: dict) -> dict:
    node_id = str(node["node_id"])
    try:
        metering_mode = str(node.get("metering_mode", "xray_stats")).strip().lower()
        if metering_mode not in {"xray_stats", "unknown"}:
            raise CollectorError("invalid_metering_mode")
        if metering_mode == "unknown":
            reason = str(node.get(
                "metering_detail",
                "per-user metering source is unavailable; usage remains Unknown",
            ))[:120]
            try:
                post_coverage(
                    endpoint, admin_token, node_id, "unknown", reason,
                    source="node-capability",
                )
            except Exception:
                return {"node_id": node_id, "status": "unknown", "reason": reason,
                        "coverage_recorded": False}
            return {"node_id": node_id, "status": "unknown", "reason": reason,
                    "coverage_recorded": True}
        result = remote_stats(str(node["ssh_host"]))
        if not result.get("ok"):
            reason = str(result.get("error") or "remote_stats_gap")[:120]
            try:
                post_coverage(endpoint, admin_token, node_id, "gap", reason)
            except Exception:
                return {"node_id": node_id, "status": "gap", "reason": reason,
                        "coverage_recorded": False}
            return {"node_id": node_id, "status": "gap", "reason": reason,
                    "coverage_recorded": True}
        observations = validate_remote_observations(result.get("observations"))
        if not observations:
            detail = "StatsService reachable but no per-user counters returned; usage is not treated as zero"
            try:
                post_coverage(endpoint, admin_token, node_id, "unknown", detail)
            except Exception:
                return {"node_id": node_id, "status": "unknown", "reason": "no_per_user_counters",
                        "coverage_recorded": False}
            return {"node_id": node_id, "status": "unknown", "reason": "no_per_user_counters",
                    "coverage_recorded": True}
        counter_epoch = str(result.get("counter_epoch") or "").strip()
        if not counter_epoch:
            raise CollectorError("counter_epoch_missing")
        payload = {
            "node_id": node_id,
            "source": "xray-stats-api",
            "counter_epoch": counter_epoch,
            "observed_at": str(result.get("observed_at") or utc_now()),
            "observations": observations,
        }
        response = post_json(endpoint, admin_token, payload)
        return {"node_id": node_id, "status": "ingested", **response}
    except Exception as exc:
        reason = _safe_error(exc)
        try:
            post_coverage(endpoint, admin_token, node_id, "gap", reason)
            recorded = True
        except Exception:
            recorded = False
        return {"node_id": node_id, "status": "gap", "reason": reason,
                "coverage_recorded": recorded}


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("invalid collector config") from exc
    if not isinstance(config.get("nodes"), list) or not config["nodes"]:
        raise SystemExit("collector config must contain nodes")
    for node in config["nodes"]:
        if not isinstance(node, dict) or not node.get("node_id") or not node.get("ssh_host"):
            raise SystemExit("collector config contains an invalid node")
        if str(node.get("metering_mode", "xray_stats")).strip().lower() not in {"xray_stats", "unknown"}:
            raise SystemExit("collector config contains an invalid metering mode")
    if not config.get("endpoint"):
        raise SystemExit("collector config must contain endpoint")
    return config


def run_once(config: dict, endpoint: str, admin_token: str) -> dict:
    results = [collect_node(endpoint, admin_token, node) for node in config["nodes"]]
    counts = {
        "attempted": len(results),
        "ingested": sum(item["status"] == "ingested" for item in results),
        "unknown": sum(item["status"] == "unknown" for item in results),
        "failed": sum(item["status"] == "gap" for item in results),
    }
    return {**counts, "nodes": results}


def run_service(config: dict, endpoint: str, admin_token: str, interval_seconds: float) -> int:
    if interval_seconds <= 0:
        raise SystemExit("interval_seconds must be positive")
    stop_event = threading.Event()

    def stop(_signum, _frame):
        stop_event.set()

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    try:
        while not stop_event.is_set():
            summary = run_once(config, endpoint, admin_token)
            try:
                post_heartbeat(endpoint, admin_token, summary)
            except Exception:
                # Coverage records remain the per-Node fallback; a heartbeat
                # failure must not stop proxy-independent collection cycles.
                pass
            print(json.dumps({"event": "collector_cycle", **summary}, separators=(",", ":")), flush=True)
            stop_event.wait(interval_seconds)
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--endpoint", help="Override the Control Plane endpoint for an SSH-tunnelled run")
    parser.add_argument("--admin-token-env", default="SPARKLINK_ADMIN_TOKEN")
    parser.add_argument("--secret-path", type=Path,
                        help="Windows LocalMachine DPAPI-protected Base64 admin-token file")
    parser.add_argument("--control-plane-ssh-host",
                        help="SSH alias used to tunnel to QQG loopback Control Plane")
    parser.add_argument("--control-plane-forward-port", type=int, default=18080)
    parser.add_argument("--interval-seconds", type=float,
                        help="Run continuously at this interval; omit for one-shot manual fallback")
    parser.add_argument("--log-path", type=Path,
                        help="Append safe collector output to this protected local log")
    args = parser.parse_args()

    def execute() -> int:
        config = load_config(args.config)
        admin_token = read_dpapi_token(args.secret_path) if args.secret_path else os.environ.get(args.admin_token_env, "")
        if not admin_token:
            raise SystemExit(f"missing {args.admin_token_env}; keep the token outside Git")
        with control_plane_tunnel(args.control_plane_ssh_host, args.control_plane_forward_port) as tunnel_endpoint:
            endpoint = tunnel_endpoint or args.endpoint or config["endpoint"]
            if args.interval_seconds is not None:
                return run_service(config, endpoint, admin_token, args.interval_seconds)
            summary = run_once(config, endpoint, admin_token)
            print(json.dumps(summary, separators=(",", ":")))
            return 0 if summary["failed"] == 0 else 1

    if args.log_path:
        args.log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with args.log_path.open("a", encoding="utf-8") as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                try:
                    return execute()
                except SystemExit as exc:
                    print(json.dumps({"event": "collector_failure", "reason": str(exc)[:120]},
                                     separators=(",", ":")), flush=True)
                    return int(exc.code) if isinstance(exc.code, int) else 1
                except Exception as exc:
                    print(json.dumps({"event": "collector_failure", "reason": _safe_error(exc)},
                                     separators=(",", ":")), flush=True)
                    return 1
    return execute()


if __name__ == "__main__":
    raise SystemExit(main())
