#!/usr/bin/env python3
"""Admit DediRock as a managed Advanced VLESS runtime.

This is a local OWNER/operator workflow.  The remote side receives a transient
identity plan through stdin, creates a root-owned rollback copy, validates and
restarts only the DediRock Xray service, and returns redacted metadata.  UUIDs,
subscription URIs, and key material stay in process memory and are never
printed or written to the repository.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import uuid
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SECRET_PATH = ROOT / "runtime" / "secrets" / "control-plane-admin-token.dpapi"
DEFAULT_DELIVERY_DIR = ROOT / "runtime" / "delivery"
DEFAULT_DEDIROCK_SSH_HOST = "dedirock-admin"
DEFAULT_CONTROL_PLANE_SSH_HOST = "sparklink-node-166"
DEFAULT_CONTROL_PLANE_FORWARD_PORT = 18082
DEFAULT_NODE_ID = "dedirock"
DEFAULT_POOL_ID = "ADVANCED"
DEFAULT_RUNTIME_FAMILY = "xray"
DEFAULT_ENDPOINT_HOST = "dedirock.enrpiglink.top"
ELIGIBLE_USERNAMES = ("root", "Hegin", "abing", "dangbin")
MANAGED_EMAIL_PREFIX = "sparklink:"
MANAGED_EMAIL_SUFFIX = ":advanced"

sys.path.insert(0, str(ROOT))
from deploy import issue_user_tokens as operator  # noqa: E402
from src.sparklink_subscription_naming import CANONICAL_DEDIROCK_ALIAS  # noqa: E402


class AdmissionError(RuntimeError):
    """A non-secret, operator-facing failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


DISCOVERY_REMOTE_SCRIPT = r'''
import hashlib,json,re,shlex,subprocess,sys
from pathlib import Path

def fail(code):
    print(json.dumps({'ok':False,'error':code},separators=(',',':')))
    raise SystemExit(0)

def run(command,timeout=30):
    try:
        return subprocess.run(command,capture_output=True,text=True,timeout=timeout)
    except Exception:
        fail('remote_command_failed')

def sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda: handle.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()

def main():
    service='xray'
    if run(['systemctl','is-active','--quiet',service],15).returncode != 0:
        fail('xray_service_not_active')
    unit=run(['systemctl','show','-p','ExecStart','--value',service],15)
    if unit.returncode != 0:
        fail('xray_unit_unreadable')
    raw=unit.stdout.strip()
    config_match=re.search(r'(?:^|\s)-config(?:=|\s+)([^\s;]+)',raw)
    if not config_match:
        config_match=re.search(r'(?:^|\s)-c(?:=|\s+)([^\s;]+)',raw)
    config_path=Path(config_match.group(1)) if config_match else Path('/etc/xray/config.json')
    binary_match=re.search(r'path=([^\s;]+)',raw)
    candidates=[]
    if binary_match:
        candidates.append(binary_match.group(1))
    for token in shlex.split(raw.replace(';',' ')):
        if token.startswith('/') and token.endswith('/xray'):
            candidates.append(token)
    candidates.extend(['/usr/local/bin/xray','/usr/local/x-ui/bin/xray-linux-amd64','/usr/bin/xray'])
    binary=next((value for value in candidates if Path(value).is_file()),None)
    if binary is None or not config_path.is_file():
        fail('xray_runtime_artifact_missing')
    try:
        config=json.loads(config_path.read_text(encoding='utf-8'))
    except Exception:
        fail('xray_config_invalid')
    reality=[]
    all_ids=[]
    for inbound in config.get('inbounds',[]):
        settings=inbound.get('settings') or {}
        clients=settings.get('clients') or []
        for client in clients:
            if isinstance(client,dict) and isinstance(client.get('id'),str):
                all_ids.append(client['id'])
        stream=inbound.get('streamSettings') or {}
        reality_settings=stream.get('realitySettings') or {}
        if (str(inbound.get('protocol','')).lower() == 'vless'
                and int(inbound.get('port',0) or 0) == 443
                and str(stream.get('security','')).lower() == 'reality'
                and isinstance(clients,list)):
            reality.append((inbound,clients,reality_settings))
    if len(reality) != 1:
        fail('reality_target_inbound_ambiguous')
    inbound,clients,reality_settings=reality[0]
    names=reality_settings.get('serverNames') or []
    short_ids=reality_settings.get('shortIds') or []
    private_key=reality_settings.get('privateKey')
    if (not isinstance(inbound.get('tag'),str) or not inbound.get('tag')
            or not isinstance(names,list) or not names or not isinstance(names[0],str)
            or not isinstance(short_ids,list) or not short_ids or not isinstance(short_ids[0],str)
            or not isinstance(private_key,str) or not private_key):
        fail('reality_settings_incomplete')
    test_flag='-config'
    test=run([binary,'run','-test',test_flag,str(config_path)],45)
    if test.returncode != 0:
        fail('xray_config_test_failed')
    key=run([binary,'x25519','-i',private_key],30)
    if key.returncode != 0:
        fail('reality_public_key_derivation_failed')
    public_key=''
    for line in key.stdout.splitlines():
        label=line.lower().replace(' ','')
        if 'privatekey' in label or 'private(key)' in label:
            continue
        if 'publickey' not in label:
            continue
        if ':' not in line:
            continue
        candidate=line.split(':',1)[1].strip().split()[0]
        if re.fullmatch(r'[A-Za-z0-9_-]{32,}',candidate):
            public_key=candidate
            break
    if not public_key:
        fail('reality_public_key_derivation_failed')
    managed=[]
    template=None
    for client in clients:
        if not isinstance(client,dict) or not isinstance(client.get('id'),str):
            continue
        email=str(client.get('email') or '')
        if email.startswith('sparklink:'):
            managed.append({'id':client['id'],'email':email})
        elif template is None:
            template={'id':client['id'],'email':email}
    if not managed and template is None:
        fail('reality_client_template_missing')
    print(json.dumps({
        'ok':True,'node_id':'dedirock','service':service,'binary':binary,
        'config_path':str(config_path),'test_flag':test_flag,
        'config_sha256':sha256(config_path),'reality_tag':inbound['tag'],
        'server_name':names[0],'short_id':short_ids[0],'public_key':public_key,
        'template':template,'managed_clients':managed,
        'all_client_ids':all_ids,
    },separators=(',',':')))

main()
'''


CLIENT_ACCEPTANCE_REMOTE_SCRIPT = r'''
import json,os,shutil,socket,subprocess,sys,tempfile,time

def result(ok,**values):
    print(json.dumps({'ok':ok,**values},separators=(',',':')))
    raise SystemExit(0)

def clean_environment():
    env=dict(os.environ)
    for key in list(env):
        if key.lower() in {'http_proxy','https_proxy','all_proxy','no_proxy'}:
            env.pop(key,None)
    return env

def free_port():
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]
    finally:
        sock.close()

def stop(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

def main():
    try:
        payload=json.load(sys.stdin)
    except Exception:
        result(False,error='client_test_payload_invalid')
    binary=str(payload.get('binary') or '')
    host=str(payload.get('host') or '')
    server_name=str(payload.get('server_name') or '')
    public_key=str(payload.get('public_key') or '')
    short_id=str(payload.get('short_id') or '')
    tests=payload.get('tests')
    if not binary or not host or not server_name or not public_key or not short_id or not isinstance(tests,list) or not tests:
        result(False,error='client_test_payload_invalid')
    passed=0
    for index,item in enumerate(tests):
        client_path=None
        process=None
        try:
            port=free_port()
            config={
                'log':{'loglevel':'none'},
                'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks',
                             'settings':{'auth':'noauth','udp':False}}],
                'outbounds':[{'protocol':'vless','settings':{'vnext':[{
                    'address':host,'port':443,'users':[{
                        'id':str(item['uuid']),'encryption':'none','flow':'xtls-rprx-vision'
                    }]
                }]},'streamSettings':{'network':'tcp','security':'reality',
                    'realitySettings':{'serverName':server_name,'fingerprint':'chrome',
                                       'publicKey':public_key,'shortId':short_id,'spiderX':'/'}}}]
            }
            fd,client_path=tempfile.mkstemp(prefix='.sparklink-dedirock-',suffix='.json',dir='/run')
            os.close(fd)
            os.chmod(client_path,0o600)
            with open(client_path,'w',encoding='utf-8') as handle:
                json.dump(config,handle,separators=(',',':'))
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            check=subprocess.run([binary,'run','-test','-config',client_path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45)
            if check.returncode != 0:
                result(False,error='client_config_test_failed',tested=index,passed=passed)
            process=subprocess.Popen([binary,'run','-config',client_path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            ready=False
            for _ in range(60):
                if process.poll() is not None:
                    break
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=0.3):
                        ready=True
                        break
                except OSError:
                    time.sleep(0.1)
            if not ready:
                result(False,error='client_listener_not_ready',tested=index,passed=passed)
            curl=subprocess.run([
                'curl','--silent','--show-error','--max-time','20',
                '--socks5-hostname',f'127.0.0.1:{port}',
                'https://www.cloudflare.com/cdn-cgi/trace','-o','/dev/null'
            ],env=clean_environment(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            if curl.returncode != 0:
                result(False,error='client_connection_failed',tested=index,passed=passed)
            passed+=1
        except subprocess.TimeoutExpired:
            result(False,error='client_connection_timeout',tested=index,passed=passed)
        except Exception:
            result(False,error='client_test_failed',tested=index,passed=passed)
        finally:
            stop(process)
            if client_path:
                try:
                    os.unlink(client_path)
                except OSError:
                    pass
    result(True,tested=len(tests),passed=passed)

main()
'''


ROLLBACK_REMOTE_SCRIPT = r'''
import hashlib,json,os,shutil,subprocess,sys
from pathlib import Path

def result(ok,**values):
    print(json.dumps({'ok':ok,**values},separators=(',',':')))
    raise SystemExit(0)

def run(command,timeout=45):
    try:
        return subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)
    except Exception:
        result(False,error='rollback_command_failed')

def sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda: handle.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()

def main():
    try:
        payload=json.load(sys.stdin)
    except Exception:
        result(False,error='rollback_payload_invalid')
    backup=Path(str(payload.get('backup') or ''))
    config=Path(str(payload.get('config_path') or ''))
    binary=str(payload.get('binary') or '')
    service=str(payload.get('service') or '')
    if (not str(backup).startswith('/var/backups/sparklink-identity-migration/')
            or backup.name != 'xray-config.json' or config != Path('/etc/xray/config.json')
            or service != 'xray' or not backup.is_file() or not config.is_file()):
        result(False,error='rollback_target_invalid')
    try:
        stat=backup.stat()
        if run(['systemctl','stop',service],30).returncode != 0:
            result(False,error='rollback_stop_failed')
        shutil.copy2(backup,config)
        os.chmod(config,stat.st_mode & 0o7777)
        if hasattr(os,'chown'):
            os.chown(config,stat.st_uid,stat.st_gid)
        if run([binary,'run','-test','-config',str(config)],45).returncode != 0:
            result(False,error='rollback_config_test_failed')
        if run(['systemctl','start',service],45).returncode != 0:
            result(False,error='rollback_start_failed')
        if run(['systemctl','is-active','--quiet',service],15).returncode != 0:
            result(False,error='rollback_service_not_active')
        result(True,restored=True,config_sha256=sha256(config))
    except Exception:
        result(False,error='rollback_failed')

main()
'''


def _encoded_remote_command(source: str) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return (
        "sudo -n python3 -c \"import base64;exec(compile(base64.b64decode('"
        + encoded
        + "'),'<sparklink-operator>','exec'))\""
    )


def remote_json(ssh_host: str, source: str, payload: dict | None = None) -> dict:
    encoded_source = base64.b64encode(source.encode("utf-8")).decode("ascii")
    payload_text = json.dumps(payload or {}, separators=(",", ":"))
    wrapper = (
        "import base64,io,json,sys\n"
        "sys.stdin=io.StringIO(" + repr(payload_text) + ")\n"
        "exec(compile(base64.b64decode(" + repr(encoded_source)
        + "),'<sparklink-operator>','exec'))\n"
    )
    try:
        result = subprocess.run(
            ["ssh.exe", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh_host,
             "sudo -n python3 -"],
            input=wrapper.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdmissionError("dedirock_remote_ssh_failed") from exc
    if result.returncode != 0:
        raise AdmissionError("dedirock_remote_command_failed")
    lines = [line.strip() for line in result.stdout.decode(errors="replace").splitlines()
             if line.strip().startswith("{")]
    if not lines:
        raise AdmissionError("dedirock_remote_response_invalid")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise AdmissionError("dedirock_remote_response_invalid") from exc
    if not isinstance(value, dict):
        raise AdmissionError("dedirock_remote_response_invalid")
    if value.get("ok") is not True:
        code = str(value.get("error") or "remote_operation_failed")
        raise AdmissionError(f"dedirock_{code}")
    return value


def managed_email(user_id: str) -> str:
    return f"{MANAGED_EMAIL_PREFIX}{user_id}{MANAGED_EMAIL_SUFFIX}"


def runtime_ref_hash(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def build_vless_uri(client_uuid: str, server_name: str, public_key: str,
                    short_id: str) -> str:
    query = urllib.parse.urlencode({
        "encryption": "none",
        "flow": "xtls-rprx-vision",
        "security": "reality",
        "sni": server_name,
        "fp": "chrome",
        "pbk": public_key,
        "sid": short_id,
        "type": "tcp",
    })
    label = urllib.parse.quote(CANONICAL_DEDIROCK_ALIAS, safe="")
    return f"vless://{client_uuid}@{DEFAULT_ENDPOINT_HOST}:443?{query}#{label}"


def validate_discovery(value: dict) -> None:
    required = {
        "node_id", "service", "binary", "config_path", "test_flag", "config_sha256",
        "reality_tag", "server_name", "short_id", "public_key", "managed_clients",
        "all_client_ids",
    }
    if not required.issubset(value) or value["node_id"] != DEFAULT_NODE_ID:
        raise AdmissionError("dedirock_discovery_invalid")
    if value["test_flag"] not in {"-config", "-c"}:
        raise AdmissionError("dedirock_test_flag_invalid")
    if value["config_path"] != "/etc/xray/config.json" or value["service"] != "xray":
        raise AdmissionError("dedirock_runtime_target_invalid")
    if (not re.fullmatch(r"[0-9a-f]{64}", str(value["config_sha256"]).lower())
            or not isinstance(value["managed_clients"], list)
            or not isinstance(value["all_client_ids"], list)):
        raise AdmissionError("dedirock_discovery_invalid")
    if any(not isinstance(item, dict) or not item.get("id") or not item.get("email")
           for item in value["managed_clients"]):
        raise AdmissionError("dedirock_managed_identity_invalid")
    if (not isinstance(value["server_name"], str) or not value["server_name"]
            or not isinstance(value["short_id"], str) or not value["short_id"]
            or not isinstance(value["public_key"], str) or not value["public_key"]):
        raise AdmissionError("dedirock_reality_settings_invalid")


def build_runtime_plan(discovery: dict, users: list[dict]) -> tuple[list[dict], list[dict], dict]:
    validate_discovery(discovery)
    by_name = {user["display_name"]: user for user in users}
    if not set(ELIGIBLE_USERNAMES).issubset(by_name):
        raise AdmissionError("dedirock_eligible_user_scope_invalid")
    managed_by_email: dict[str, dict] = {}
    for item in discovery["managed_clients"]:
        email = str(item["email"])
        if email in managed_by_email:
            raise AdmissionError("dedirock_managed_email_duplicate")
        try:
            uuid.UUID(str(item["id"]))
        except ValueError as exc:
            raise AdmissionError("dedirock_managed_uuid_invalid") from exc
        managed_by_email[email] = item
    all_ids = {str(value) for value in discovery["all_client_ids"]}
    managed_entries: list[dict] = []
    migration_entries: list[dict] = []
    client_tests: list[dict] = []
    missing_users = []
    template = discovery.get("template")
    if template:
        try:
            uuid.UUID(str(template["id"]))
        except (KeyError, ValueError) as exc:
            raise AdmissionError("dedirock_template_invalid") from exc
    for username in ELIGIBLE_USERNAMES:
        user = by_name[username]
        if user["plan"] not in {"Basic", "Plus"}:
            raise AdmissionError("dedirock_user_plan_not_entitled")
        email = managed_email(user["user_id"])
        existing = managed_by_email.get(email)
        if existing is not None:
            client_uuid = str(existing["id"])
        else:
            if template is None:
                raise AdmissionError("dedirock_client_template_missing")
            client_uuid = str(uuid.uuid4())
            while client_uuid in all_ids:
                client_uuid = str(uuid.uuid4())
            all_ids.add(client_uuid)
            migration_entries.append({
                "source_entry_id": user["user_id"],
                "source_tag": discovery["reality_tag"],
                "old_uuid": str(template["id"]),
                "new_uuid": client_uuid,
                "new_email": email,
            })
            missing_users.append(username)
        try:
            uuid.UUID(client_uuid)
        except ValueError as exc:
            raise AdmissionError("dedirock_client_uuid_invalid") from exc
        uri = build_vless_uri(
            client_uuid, discovery["server_name"], discovery["public_key"],
            discovery["short_id"],
        )
        managed_entries.append({
            "user_id": user["user_id"],
            "runtime_ref_hash": runtime_ref_hash(email),
            "runtime_family": DEFAULT_RUNTIME_FAMILY,
            "protocol": "vless",
            "credential_kind": "managed",
            "uri": uri,
            "minimum_plan": "Basic",
        })
        client_tests.append({"uuid": client_uuid})
    if len({item["runtime_ref_hash"] for item in managed_entries}) != len(managed_entries):
        raise AdmissionError("dedirock_runtime_mapping_duplicate")
    metadata = {
        "missing_users": missing_users,
        "managed_users": len(managed_entries),
        "client_tests": client_tests,
        "template_used": bool(migration_entries),
    }
    return managed_entries, migration_entries, metadata


def apply_runtime(ssh_host: str, discovery: dict, migration_entries: list[dict]) -> dict | None:
    if not migration_entries:
        return None
    plan = {
        "node_id": DEFAULT_NODE_ID,
        "config_path": discovery["config_path"],
        "binary": discovery["binary"],
        "service": discovery["service"],
        "test_flag": discovery["test_flag"],
        "expected_config_sha256": discovery["config_sha256"],
        "entries": migration_entries,
    }
    # The migration utility is sent as code only; it is not installed on the
    # Node and the transient plan is supplied over stdin.
    source = (ROOT / "deploy" / "apply_xray_identity_migration.py").read_text(encoding="utf-8")
    value = remote_json(ssh_host, source, plan)
    if value.get("node_id") != DEFAULT_NODE_ID:
        raise AdmissionError("dedirock_runtime_apply_response_invalid")
    expected = len(migration_entries)
    if int(value.get("added", -1)) + int(value.get("already_present", -1)) != expected:
        raise AdmissionError("dedirock_runtime_apply_count_mismatch")
    return value


def client_acceptance(ssh_host: str, discovery: dict, client_tests: list[dict]) -> dict:
    payload = {
        "binary": discovery["binary"],
        "host": DEFAULT_ENDPOINT_HOST,
        "server_name": discovery["server_name"],
        "public_key": discovery["public_key"],
        "short_id": discovery["short_id"],
        "tests": client_tests,
    }
    return remote_json(ssh_host, CLIENT_ACCEPTANCE_REMOTE_SCRIPT, payload)


def rollback_runtime(ssh_host: str, apply_result: dict) -> dict:
    backup = str(apply_result.get("backup") or "")
    if not backup.startswith("/var/backups/sparklink-identity-migration/"):
        raise AdmissionError("dedirock_rollback_target_invalid")
    return remote_json(ssh_host, ROLLBACK_REMOTE_SCRIPT, {
        "backup": backup,
        "config_path": "/etc/xray/config.json",
        "binary": apply_result.get("binary", "/usr/local/bin/xray"),
        "service": "xray",
    })


def cp_endpoint_args(args: argparse.Namespace) -> argparse.Namespace:
    return SimpleNamespace(
        endpoint=None,
        ssh_host=args.control_plane_ssh_host,
        forward_port=args.control_plane_forward_port,
    )


def admit_control_plane(args: argparse.Namespace, entries: list[dict]) -> dict:
    admin_token = operator._admin_token(Path(args.secret_path))
    payload = {
        "node_id": DEFAULT_NODE_ID,
        "pool_id": DEFAULT_POOL_ID,
        "display_name": "DediRock Advanced serving Node",
        "qualification": "verified",
        "source": "dedirock-runtime-admission-2026-08-30",
        "metering_status": "unknown",
        "supported_protocols": ["vless"],
        "detail": "direct Reality VLESS ingress with managed HyTru/WARP egress verified; DediRock per-user Stats source unavailable; Usage Unknown; quota unavailable",
        "entries": entries,
    }
    with operator.selected_endpoint(cp_endpoint_args(args)) as endpoint:
        return operator.admin_json(
            endpoint, admin_token, "/api/admin/runtime-admission", "POST", payload
        )


def parse_projection(subscription_url_value: str) -> list[str]:
    status, raw = operator._request_url(
        subscription_url_value,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
    )
    if status != 200:
        raise AdmissionError("dedirock_public_subscription_fetch_failed")
    try:
        decoded = base64.b64decode(raw.strip(), validate=True).decode("utf-8")
    except Exception as exc:
        raise AdmissionError("dedirock_public_subscription_invalid") from exc
    lines = [line for line in decoded.splitlines() if line]
    if any("anytls" in line.lower() for line in lines):
        raise AdmissionError("dedirock_public_subscription_anytls_present")
    return lines


def verify_public_and_owner(args: argparse.Namespace, users: list[dict]) -> dict:
    delivery_dir = Path(args.delivery_dir)
    checks = {"eligible_users": 0, "public_projection": 0, "owner_self_scope": 0}
    admin_token = operator._admin_token(Path(args.secret_path))
    cp_args = cp_endpoint_args(args)
    with operator.selected_endpoint(cp_args) as endpoint:
        refreshed = operator.read_admin_users(endpoint, admin_token)
        by_name = {item["display_name"]: item for item in refreshed}
        for username in ELIGIBLE_USERNAMES:
            user = by_name.get(username)
            if user is None:
                raise AdmissionError("dedirock_admin_user_missing")
            bundle = operator.read_bundle(operator.user_bundle_path(username, delivery_dir))
            subscription_url_value = bundle.get("subscription_url")
            if not isinstance(subscription_url_value, str):
                raise AdmissionError("dedirock_delivery_bundle_incomplete")
            operator.verify_public_subscription_projection(
                subscription_url_value, user["plan"], user["subscription_status"],
                user["subscription_entry_count"], user["subscription_pool_ids"],
                user["subscription_protocols"], args.public_subscription_base_url,
            )
            lines = parse_projection(subscription_url_value)
            if not any(urllib.parse.urlsplit(line).hostname == DEFAULT_ENDPOINT_HOST
                       and urllib.parse.urlsplit(line).port == 443
                       for line in lines):
                raise AdmissionError("dedirock_public_projection_missing")
            checks["eligible_users"] += 1
            checks["public_projection"] += 1
        root = by_name.get("root")
        if root is None:
            raise AdmissionError("dedirock_root_missing")
        root_bundle = operator.read_bundle(operator.user_bundle_path("root", delivery_dir))
        root_token = root_bundle.get("portal_access_token") or root_bundle.get("portal_token")
        if not isinstance(root_token, str) or not root_token:
            raise AdmissionError("dedirock_root_portal_bundle_incomplete")
        status, raw = operator._request(endpoint, "/api/me",
                                         headers={"Authorization": f"Bearer {root_token}"})
        if status != 200:
            raise AdmissionError("dedirock_owner_portal_fetch_failed")
        try:
            view = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AdmissionError("dedirock_owner_portal_response_invalid") from exc
        if (not isinstance(view, dict) or view.get("user_id") != root["user_id"]
                or view.get("plan") != "Plus" or view.get("role") != "OWNER"
                or "users" in view):
            raise AdmissionError("dedirock_owner_self_scope_failed")
        pool_ids = {item.get("pool_id") for item in view.get("pools", []) if isinstance(item, dict)}
        if pool_ids != {"STANDARD", "ADVANCED", "PREMIUM"}:
            raise AdmissionError("dedirock_owner_pool_acceptance_failed")
        advanced = next((item for item in view.get("pools", [])
                         if isinstance(item, dict) and item.get("pool_id") == "ADVANCED"), None)
        if not isinstance(advanced, dict) or advanced.get("coverage_status") != "unknown":
            raise AdmissionError("dedirock_owner_metering_semantics_failed")
        checks["owner_self_scope"] = 1
    return checks


def reconcile_deliveries(args: argparse.Namespace) -> None:
    """Refresh all six protected bundles without rotating any token."""
    reconcile_args = SimpleNamespace(
        secret_path=Path(args.secret_path), delivery_dir=Path(args.delivery_dir),
        portal_url=args.portal_url,
        public_subscription_base_url=args.public_subscription_base_url,
        endpoint=None, ssh_host=args.control_plane_ssh_host,
        forward_port=args.control_plane_forward_port,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        result = operator.reconcile(reconcile_args)
    if result != 0:
        raise AdmissionError("delivery_reconciliation_failed")


def run(args: argparse.Namespace) -> dict:
    discovery = remote_json(args.dedirock_ssh_host, DISCOVERY_REMOTE_SCRIPT)
    validate_discovery(discovery)
    admin_token = operator._admin_token(Path(args.secret_path))
    with operator.selected_endpoint(cp_endpoint_args(args)) as endpoint:
        users = operator.read_admin_users(endpoint, admin_token)
    entries, migration_entries, metadata = build_runtime_plan(discovery, users)
    apply_result = apply_runtime(args.dedirock_ssh_host, discovery, migration_entries)
    try:
        acceptance = client_acceptance(args.dedirock_ssh_host, discovery, metadata["client_tests"])
    except AdmissionError:
        if apply_result is not None:
            try:
                rollback_runtime(args.dedirock_ssh_host, apply_result)
            except AdmissionError as rollback_error:
                raise AdmissionError(f"dedirock_acceptance_failed_rollback_failed_{rollback_error.code}")
        raise
    if int(acceptance.get("tested", -1)) != len(metadata["client_tests"]):
        raise AdmissionError("dedirock_client_acceptance_count_mismatch")
    cp_result = admit_control_plane(args, entries)
    reconcile_deliveries(args)
    verification = verify_public_and_owner(args, users)
    return {
        "ok": True,
        "node_id": DEFAULT_NODE_ID,
        "pool_id": DEFAULT_POOL_ID,
        "managed_users": metadata["managed_users"],
        "runtime_identities_added": int((apply_result or {}).get("added", 0)),
        "runtime_identities_reused": int((apply_result or {}).get("already_present", 0))
            + len(metadata["client_tests"]) - len(migration_entries),
        "client_tests": int(acceptance["tested"]),
        "client_acceptance": "passed",
        "control_plane": {
            "credentials_created": cp_result.get("credentials_created"),
            "credentials_reused": cp_result.get("credentials_reused"),
            "subscriptions_created": cp_result.get("subscriptions_created"),
            "subscriptions_reused": cp_result.get("subscriptions_reused"),
            "metering_status": cp_result.get("metering_status"),
            "quota_status": cp_result.get("quota_status"),
        },
        "delivery": verification,
        "rollback_backup": (apply_result or {}).get("backup"),
        "plaintext_not_printed": True,
        "legacy_access_changed": False,
        "hard_quota": "disabled",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Admit DediRock as managed Advanced VLESS")
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_SECRET_PATH)
    parser.add_argument("--delivery-dir", type=Path, default=DEFAULT_DELIVERY_DIR)
    parser.add_argument("--dedirock-ssh-host", default=DEFAULT_DEDIROCK_SSH_HOST)
    parser.add_argument("--control-plane-ssh-host", default=DEFAULT_CONTROL_PLANE_SSH_HOST)
    parser.add_argument("--control-plane-forward-port", type=int, default=DEFAULT_CONTROL_PLANE_FORWARD_PORT)
    parser.add_argument("--portal-url", default="https://spark.enrpiglink.top")
    parser.add_argument("--public-subscription-base-url", default="https://sub.enrpiglink.top")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(json.dumps(run(args), separators=(",", ":")))
        return 0
    except AdmissionError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "plaintext_not_printed": True}, separators=(",", ":")))
        return 1
    except Exception:
        print(json.dumps({"ok": False, "error": "dedirock_admission_failed", "plaintext_not_printed": True}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
