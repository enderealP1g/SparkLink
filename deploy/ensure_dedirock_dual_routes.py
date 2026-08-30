#!/usr/bin/env python3
"""Ensure DediRock Advanced exposes separate Origin and HyTru routes.

The existing ``sparklink:<user>:advanced`` identities remain the HyTru path
for backward compatibility. This operator adds one separately identified
``:origin`` identity per eligible User and routes it to the native ``direct``
outbound. Runtime UUIDs and URI values stay in process memory or transient
remote stdin; only redacted counts, hashes, and status are printed.

The remote mutation is SHA-guarded, backed up root-only, validated, and
automatically rolled back if the service cannot recover. A second isolated
client acceptance checks Cloudflare ``warp=off`` for Origin and ``warp=on``
for HyTru before the Control Plane projection is extended.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SSH_HOST = "dedirock-admin"
DEFAULT_CONTROL_PLANE_SSH_HOST = "sparklink-node-166"
DEFAULT_CONTROL_PLANE_FORWARD_PORT = 18082
DEFAULT_SECRET_PATH = ROOT / "runtime" / "secrets" / "control-plane-admin-token.dpapi"
DEFAULT_DELIVERY_DIR = ROOT / "runtime" / "delivery"
DEFAULT_PUBLIC_SUBSCRIPTION_BASE_URL = "https://sub.enrpiglink.top"
DEFAULT_NODE_ID = "dedirock"
DEFAULT_POOL_ID = "ADVANCED"
DEFAULT_ENDPOINT_HOST = "dedirock.enrpiglink.top"
ELIGIBLE_USERNAMES = ("root", "Hegin", "abing", "dangbin")
SOURCE = "dedirock-dual-egress-2026-08-30"
HYTRU_EMAIL_RE = re.compile(r"^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced$")
ORIGIN_EMAIL_RE = re.compile(r"^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced:origin$")

sys.path.insert(0, str(ROOT))
from deploy import admit_dedirock  # noqa: E402
from deploy import issue_user_tokens as operator  # noqa: E402
from src.sparklink_control_plane import PLAN_ORDER  # noqa: E402
from src.sparklink_subscription_naming import (  # noqa: E402
    CANONICAL_DEDIROCK_HYTRU_ALIAS,
    CANONICAL_DEDIROCK_ORIGIN_ALIAS,
    alias_from_uri,
)


class DualRouteError(RuntimeError):
    """A non-secret operator-facing failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def managed_email(user_id: str, route: str = "hytru") -> str:
    suffix = ":origin" if route.lower() == "origin" else ""
    return f"sparklink:{user_id}:advanced{suffix}"


def runtime_ref_hash(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def _remote_json(ssh_host: str, source: str, payload: dict | None = None) -> dict:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    payload_text = json.dumps(payload or {}, separators=(",", ":"))
    wrapper = (
        "import base64,io,json,sys\n"
        "sys.stdin=io.StringIO(" + repr(payload_text) + ")\n"
        "exec(compile(base64.b64decode(" + repr(encoded)
        + "),'<sparklink-dedirock-dual>','exec'))\n"
    )
    try:
        result = subprocess.run(
            [
                "ssh.exe", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                ssh_host, "sudo -n python3 -",
            ],
            input=wrapper.encode("utf-8"), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=240, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DualRouteError("remote_ssh_failed") from exc
    lines = [
        line.strip() for line in result.stdout.decode(errors="replace").splitlines()
        if line.strip().startswith("{")
    ]
    if not lines:
        raise DualRouteError("remote_response_invalid")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise DualRouteError("remote_response_invalid") from exc
    if not isinstance(value, dict) or value.get("ok") is not True:
        code = (
            str(value.get("error") or "remote_operation_failed")
            if isinstance(value, dict) else "remote_operation_failed"
        )
        suffix = "_rolled_back" if isinstance(value, dict) and value.get("rolled_back") else ""
        raise DualRouteError(code + suffix)
    return value


REMOTE_INSPECT_SCRIPT = r'''
import hashlib,json,re,subprocess
from pathlib import Path

CONFIG=Path('/etc/xray/config.json')
BINARY='/usr/local/bin/xray'
SERVICE='xray'
HYTRU_RE=re.compile(r'^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced$')
ORIGIN_RE=re.compile(r'^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced:origin$')

def result(ok,**values):
    print(json.dumps({'ok':ok,**values},separators=(',',':')),flush=True)
    raise SystemExit(0)

def public_key(private_key):
    value=subprocess.run([BINARY,'x25519','-i',private_key],capture_output=True,text=True,timeout=30)
    if value.returncode != 0:
        return ''
    for line in value.stdout.splitlines():
        normalized=line.lower().replace(' ','')
        if 'publickey' not in normalized or ':' not in line:
            continue
        candidate=line.split(':',1)[1].strip().split()[0]
        if re.fullmatch(r'[A-Za-z0-9_-]{32,}',candidate):
            return candidate
    return ''

if not CONFIG.is_file():
    result(False,error='config_missing')
try:
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
except Exception:
    result(False,error='config_invalid')
if subprocess.run(['systemctl','is-active','--quiet',SERVICE],timeout=15).returncode != 0:
    result(False,error='service_not_active')
test=subprocess.run([BINARY,'run','-test','-config',str(CONFIG)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45)
if test.returncode != 0:
    result(False,error='config_test_failed')

reality=None
managed={'hytru':{},'origin':{}}
all_ids=set()
inbound_count=0
for inbound in config.get('inbounds',[]):
    if not isinstance(inbound,dict):
        continue
    stream=inbound.get('streamSettings') or {}
    reality_settings=stream.get('realitySettings') or {}
    is_reality=(str(inbound.get('protocol','')).lower()=='vless'
                and int(inbound.get('port',0) or 0)==443
                and str(stream.get('security','')).lower()=='reality')
    if not is_reality:
        continue
    inbound_count += 1
    if reality is not None:
        result(False,error='multiple_reality_inbounds')
    names=reality_settings.get('serverNames') or []
    short_ids=reality_settings.get('shortIds') or []
    private_key=str(reality_settings.get('privateKey') or '')
    reality={'tag':str(inbound.get('tag') or ''),
             'server_name':str(names[0]) if names else '',
             'short_id':str(short_ids[0]) if short_ids else '',
             'public_key':public_key(private_key)}
    settings=inbound.get('settings') or {}
    clients=settings.get('clients') or []
    for client in clients:
        if not isinstance(client,dict):
            continue
        client_id=str(client.get('id') or '')
        if client_id:
            all_ids.add(client_id)
        email=str(client.get('email') or '')
        match=HYTRU_RE.fullmatch(email)
        kind='hytru'
        if not match:
            match=ORIGIN_RE.fullmatch(email)
            kind='origin'
        if not match:
            continue
        user=match.group('user')
        if user in managed[kind]:
            result(False,error='duplicate_managed_identity')
        managed[kind][user]={'user':user,'email':email,'uuid':client_id,
                             'inbound_tag':str(inbound.get('tag') or '')}
if reality is None or inbound_count != 1:
    result(False,error='reality_inbound_missing')
if not reality['tag'] or not reality['server_name'] or not reality['short_id'] or not reality['public_key']:
    result(False,error='reality_parameters_unavailable')

outbounds=[]
for item in config.get('outbounds',[]):
    if isinstance(item,dict):
        outbounds.append({'tag':str(item.get('tag') or ''),'protocol':str(item.get('protocol') or '')})
direct=next((item for item in config.get('outbounds',[]) if isinstance(item,dict) and item.get('tag')=='direct'),None)
warp=next((item for item in config.get('outbounds',[]) if isinstance(item,dict) and item.get('tag')=='warp'),None)
if not isinstance(direct,dict) or str(direct.get('protocol','')).lower()!='freedom':
    result(False,error='native_direct_outbound_missing')
if not isinstance(warp,dict) or str(warp.get('protocol','')).lower()!='wireguard':
    result(False,error='warp_wireguard_outbound_missing')

routes={}
for kind in ('hytru','origin'):
    for item in managed[kind].values():
        routes[item['email']]=[]
for rule in (config.get('routing') or {}).get('rules') or []:
    if not isinstance(rule,dict) or not isinstance(rule.get('user'),list):
        continue
    for selector in rule['user']:
        selector=str(selector)
        if selector in routes:
            routes[selector].append(str(rule.get('outboundTag') or ''))
stat=CONFIG.stat()
result(True,config_path=str(CONFIG),binary=BINARY,service=SERVICE,test_flag='-config',
       config_sha256=hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
       config_mode=oct(stat.st_mode & 0o7777),config_uid=stat.st_uid,config_gid=stat.st_gid,
       managed=managed,route_tags=routes,all_client_ids=sorted(all_ids),reality=reality,
       outbounds=outbounds,xui_db_present=Path('/etc/x-ui/x-ui.db').is_file())
'''


REMOTE_APPLY_SCRIPT = r'''
import copy,datetime,hashlib,json,os,re,shutil,subprocess,sys,tempfile,uuid
from pathlib import Path

CONFIG=Path('/etc/xray/config.json')
BINARY='/usr/local/bin/xray'
SERVICE='xray'
BACKUP_ROOT=Path('/var/backups/sparklink-identity-migration')
HYTRU_RE=re.compile(r'^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced$')
ORIGIN_RE=re.compile(r'^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced:origin$')
USER_RE=re.compile(r'^[A-Za-z0-9_.-]+$')

def result(ok,**values):
    print(json.dumps({'ok':ok,**values},separators=(',',':')),flush=True)
    raise SystemExit(0)

def sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()

def run(command,timeout=60):
    return subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)

def restore(backup,source_stat):
    shutil.copy2(backup,CONFIG)
    os.chmod(CONFIG,source_stat.st_mode & 0o7777)
    os.chown(CONFIG,source_stat.st_uid,source_stat.st_gid)
    if run([BINARY,'run','-test','-config',str(CONFIG)],45).returncode != 0:
        raise RuntimeError('rollback_config_test_failed')
    if run(['systemctl','restart',SERVICE],60).returncode != 0 or run(['systemctl','is-active','--quiet',SERVICE],15).returncode != 0:
        raise RuntimeError('rollback_service_failed')

try:
    payload=json.load(sys.stdin)
except Exception:
    result(False,error='apply_payload_invalid')
expected_sha=str(payload.get('expected_config_sha256') or '').lower()
expected_users=payload.get('expected_user_ids')
identity_plan=payload.get('origin_identities')
if (not re.fullmatch(r'[0-9a-f]{64}',expected_sha)
        or not isinstance(expected_users,list) or not expected_users
        or any(not isinstance(item,str) or not USER_RE.fullmatch(item) for item in expected_users)
        or not isinstance(identity_plan,list) or len(identity_plan)!=len(expected_users)):
    result(False,error='apply_payload_invalid')
if not CONFIG.is_file() or sha256(CONFIG).lower()!=expected_sha:
    result(False,error='config_changed_since_inspect')
try:
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
except Exception:
    result(False,error='config_invalid')
if Path('/etc/x-ui/x-ui.db').is_file():
    result(False,error='persistent_panel_requires_separate_migration')

source_by_user={}
hytru_by_user={}
origin_by_user={}
all_ids=set()
reality_inbound_count=0
for inbound in config.get('inbounds',[]):
    if not isinstance(inbound,dict):
        continue
    stream=inbound.get('streamSettings') or {}
    is_reality=(str(inbound.get('protocol','')).lower()=='vless'
                and int(inbound.get('port',0) or 0)==443
                and str(stream.get('security','')).lower()=='reality')
    if not is_reality:
        continue
    reality_inbound_count += 1
    settings=inbound.get('settings') or {}
    clients=settings.get('clients') or []
    if not isinstance(clients,list):
        result(False,error='reality_clients_invalid')
    for client in clients:
        if not isinstance(client,dict):
            continue
        client_id=str(client.get('id') or '')
        if client_id:
            all_ids.add(client_id)
        email=str(client.get('email') or '')
        match=HYTRU_RE.fullmatch(email)
        target=hytru_by_user
        if not match:
            match=ORIGIN_RE.fullmatch(email)
            target=origin_by_user
        if not match:
            continue
        user=match.group('user')
        if user in target:
            result(False,error='duplicate_managed_identity')
        target[user]={'email':email,'uuid':client_id,'inbound':inbound,'clients':clients,'client':client}
        if target is hytru_by_user:
            source_by_user[user]=target[user]
if reality_inbound_count != 1:
    result(False,error='reality_inbound_missing')
expected_set=set(expected_users)
if set(hytru_by_user) != expected_set:
    result(False,error='hytru_identity_set_changed')
if set(origin_by_user)-expected_set:
    result(False,error='unexpected_origin_identity')
origin_missing_count=len(expected_set-set(origin_by_user))

plan_by_user={}
for item in identity_plan:
    if not isinstance(item,dict):
        result(False,error='apply_identity_invalid')
    user=str(item.get('user_id') or '')
    email=str(item.get('email') or '')
    new_uuid=str(item.get('uuid') or '')
    source_email=str(item.get('source_email') or '')
    if (user in plan_by_user or user not in expected_set
            or source_email != str((hytru_by_user.get(user) or {}).get('email') or '')
            or email != f'sparklink:{user}:advanced:origin'
            or not USER_RE.fullmatch(user)):
        result(False,error='apply_identity_invalid')
    try:
        uuid.UUID(new_uuid)
    except ValueError:
        result(False,error='apply_uuid_invalid')
    plan_by_user[user]={'email':email,'uuid':new_uuid}
if set(plan_by_user) != expected_set:
    result(False,error='apply_identity_set_invalid')

for user,item in plan_by_user.items():
    existing=origin_by_user.get(user)
    if existing is not None:
        if existing['uuid'] != item['uuid']:
            result(False,error='existing_origin_identity_conflict')
        continue
    if item['uuid'] in all_ids:
        result(False,error='origin_uuid_collision')
    source=source_by_user[user]
    clone=copy.deepcopy(source['client'])
    clone['id']=item['uuid']
    clone['email']=item['email']
    source['clients'].append(clone)
    all_ids.add(item['uuid'])
    origin_by_user[user]={'email':item['email'],'uuid':item['uuid'],'inbound':source['inbound'],'clients':source['clients'],'client':clone}

direct=next((item for item in config.get('outbounds',[]) if isinstance(item,dict) and item.get('tag')=='direct'),None)
warp=next((item for item in config.get('outbounds',[]) if isinstance(item,dict) and item.get('tag')=='warp'),None)
if not isinstance(direct,dict) or str(direct.get('protocol','')).lower()!='freedom':
    result(False,error='native_direct_outbound_missing')
if not isinstance(warp,dict) or str(warp.get('protocol','')).lower()!='wireguard':
    result(False,error='warp_wireguard_outbound_missing')
routing=config.get('routing')
if not isinstance(routing,dict) or not isinstance(routing.get('rules'),list):
    result(False,error='routing_rules_missing')
managed_route={}
for user in sorted(expected_set):
    managed_route[hytru_by_user[user]['email']]='warp'
    managed_route[origin_by_user[user]['email']]='direct'
rules=routing['rules']
cleaned=[]
for rule in rules:
    if not isinstance(rule,dict):
        result(False,error='routing_rule_invalid')
    selectors=rule.get('user')
    if not isinstance(selectors,list):
        cleaned.append(copy.deepcopy(rule))
        continue
    overlap=set(str(value) for value in selectors).intersection(managed_route)
    if not overlap:
        cleaned.append(copy.deepcopy(rule))
        continue
    if rule.get('type')!='field' or not set(rule).issubset({'type','user','outboundTag'}):
        result(False,error='managed_route_rule_shape_ambiguous')
    remaining=[str(value) for value in selectors if str(value) not in managed_route]
    if remaining:
        updated=copy.deepcopy(rule)
        updated['user']=remaining
        cleaned.append(updated)
first_user_rule=len(cleaned)
for index,rule in enumerate(cleaned):
    if isinstance(rule.get('user'),list) and rule['user']:
        first_user_rule=index
        break
new_rules=[
    {'type':'field','user':sorted(origin_by_user[user]['email'] for user in expected_set),'outboundTag':'direct'},
    {'type':'field','user':sorted(hytru_by_user[user]['email'] for user in expected_set),'outboundTag':'warp'},
]
cleaned[first_user_rule:first_user_rule]=new_rules
candidate=copy.deepcopy(config)
candidate['routing']['rules']=cleaned

route_check={email:[] for email in managed_route}
for rule in candidate['routing']['rules']:
    if not isinstance(rule,dict) or not isinstance(rule.get('user'),list):
        continue
    for selector in rule['user']:
        selector=str(selector)
        if selector in route_check:
            route_check[selector].append(str(rule.get('outboundTag') or ''))
if any(route_check[email] != [expected] for email,expected in managed_route.items()):
    result(False,error='dual_route_verification_failed')

before_sha=expected_sha
if candidate == config:
    result(True,changed=False,backup='',before_sha256=before_sha,after_sha256=before_sha,
           managed_count=len(expected_set)*2,origin_added=0,route='dual')

source_stat=CONFIG.stat()
backup=None
try:
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_dir=BACKUP_ROOT/(stamp+'-dedirock-dual-egress')
    backup_dir.mkdir(mode=0o700,parents=True,exist_ok=False)
    os.chmod(backup_dir,0o700)
    backup=backup_dir/'xray-config.json'
    shutil.copy2(CONFIG,backup)
    os.chmod(backup,0o600)
    os.chown(backup,0,0)
    if sha256(backup).lower()!=before_sha:
        raise RuntimeError('backup_hash_mismatch')
    fd,tmp=tempfile.mkstemp(prefix='.config.json.sparklink-',suffix='.tmp',dir=str(CONFIG.parent))
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(candidate,handle,ensure_ascii=False,separators=(',',':'))
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp,CONFIG)
        os.chmod(CONFIG,source_stat.st_mode & 0o7777)
        os.chown(CONFIG,source_stat.st_uid,source_stat.st_gid)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if run([BINARY,'run','-test','-config',str(CONFIG)],45).returncode != 0:
        raise RuntimeError('config_test_failed')
    if run(['systemctl','restart',SERVICE],60).returncode != 0 or run(['systemctl','is-active','--quiet',SERVICE],15).returncode != 0:
        raise RuntimeError('service_restart_failed')
    result(True,changed=True,backup=str(backup),before_sha256=before_sha,after_sha256=sha256(CONFIG),
           managed_count=len(expected_set)*2,origin_added=origin_missing_count,route='dual')
except Exception:
    rolled_back=False
    if backup is not None:
        try:
            restore(backup,source_stat)
            rolled_back=True
        except Exception:
            pass
    result(False,error='dual_route_apply_failed',rolled_back=rolled_back)
'''


REMOTE_ROLLBACK_SCRIPT = r'''
import hashlib,json,os,re,shutil,subprocess,sys
from pathlib import Path

CONFIG=Path('/etc/xray/config.json')
ROOT=Path('/var/backups/sparklink-identity-migration')

def result(ok,**values):
    print(json.dumps({'ok':ok,**values},separators=(',',':')),flush=True)
    raise SystemExit(0)

def sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()

try:
    payload=json.load(sys.stdin)
except Exception:
    result(False,error='rollback_payload_invalid')
backup=Path(str(payload.get('backup') or ''))
expected=str(payload.get('expected_after_sha256') or '').lower()
if (not str(backup).startswith(str(ROOT)+'/')
        or backup.name!='xray-config.json'
        or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-dedirock-dual-egress',backup.parent.name)
        or not backup.is_file() or not CONFIG.is_file()
        or not re.fullmatch(r'[0-9a-f]{64}',expected)
        or sha256(CONFIG).lower()!=expected):
    result(False,error='rollback_target_invalid')
source_stat=CONFIG.stat()
try:
    shutil.copy2(backup,CONFIG)
    os.chmod(CONFIG,source_stat.st_mode & 0o7777)
    os.chown(CONFIG,source_stat.st_uid,source_stat.st_gid)
    if subprocess.run(['/usr/local/bin/xray','run','-test','-config',str(CONFIG)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45).returncode != 0:
        result(False,error='rollback_config_test_failed')
    if subprocess.run(['systemctl','restart','xray'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60).returncode != 0 or subprocess.run(['systemctl','is-active','--quiet','xray'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15).returncode != 0:
        result(False,error='rollback_service_failed')
    result(True,restored=True,config_sha256=sha256(CONFIG))
except Exception:
    result(False,error='rollback_failed')
'''


REMOTE_ACCEPTANCE_SCRIPT = r'''
import json,os,re,socket,subprocess,tempfile,time
from pathlib import Path

CONFIG=Path('/etc/xray/config.json')
BINARY='/usr/local/bin/xray'
HYTRU_RE=re.compile(r'^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced$')
ORIGIN_RE=re.compile(r'^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced:origin$')

def result(ok,**values):
    print(json.dumps({'ok':ok,**values},separators=(',',':')),flush=True)
    raise SystemExit(0)

def clean_env():
    env=dict(os.environ)
    for key in list(env):
        if key.lower() in {'http_proxy','https_proxy','all_proxy','no_proxy'}:
            env.pop(key,None)
    return env

def stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

def public_key(private_key):
    value=subprocess.run([BINARY,'x25519','-i',private_key],capture_output=True,text=True,timeout=30)
    if value.returncode != 0:
        return ''
    for line in value.stdout.splitlines():
        if 'publickey' not in line.lower().replace(' ','') or ':' not in line:
            continue
        candidate=line.split(':',1)[1].strip().split()[0]
        if re.fullmatch(r'[A-Za-z0-9_-]{32,}',candidate):
            return candidate
    return ''

try:
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
except Exception:
    result(False,error='config_invalid')
reality=None
clients={'hytru':{},'origin':{}}
routes={}
for inbound in config.get('inbounds',[]):
    if not isinstance(inbound,dict):
        continue
    stream=inbound.get('streamSettings') or {}
    reality_settings=stream.get('realitySettings') or {}
    is_reality=(str(inbound.get('protocol','')).lower()=='vless'
                and int(inbound.get('port',0) or 0)==443
                and str(stream.get('security','')).lower()=='reality')
    if not is_reality:
        continue
    names=reality_settings.get('serverNames') or []
    short_ids=reality_settings.get('shortIds') or []
    reality={'server_name':str(names[0]) if names else '',
             'short_id':str(short_ids[0]) if short_ids else '',
             'public_key':public_key(str(reality_settings.get('privateKey') or ''))}
    for client in (inbound.get('settings') or {}).get('clients') or []:
        if not isinstance(client,dict):
            continue
        email=str(client.get('email') or '')
        match=HYTRU_RE.fullmatch(email)
        kind='hytru'
        if not match:
            match=ORIGIN_RE.fullmatch(email)
            kind='origin'
        if not match:
            continue
        user=match.group('user')
        clients[kind][user]={'uuid':str(client.get('id') or ''),'email':email}
        routes[email]=[]
for rule in (config.get('routing') or {}).get('rules') or []:
    if not isinstance(rule,dict) or not isinstance(rule.get('user'),list):
        continue
    for selector in rule['user']:
        selector=str(selector)
        if selector in routes:
            routes[selector].append(str(rule.get('outboundTag') or ''))
if (not reality or not reality['server_name'] or not reality['short_id'] or not reality['public_key']
        or not clients['hytru'] or not clients['origin']):
    result(False,error='acceptance_fixture_missing')
expected_users=sorted(set(clients['hytru']) | set(clients['origin']))
if set(clients['hytru']) != set(clients['origin']):
    result(False,error='dual_identity_set_mismatch')
if any(routes.get(clients['hytru'][user]['email']) != ['warp'] for user in expected_users):
    result(False,error='hytru_route_shape_invalid')
if any(routes.get(clients['origin'][user]['email']) != ['direct'] for user in expected_users):
    result(False,error='origin_route_shape_invalid')

results=[]
for user in expected_users:
    for kind in ('origin','hytru'):
        item=clients[kind][user]
        listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
        listener.close()
        config_path=None
        process=None
        try:
            client_config={
                'log':{'loglevel':'none'},
                'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks','settings':{'auth':'noauth','udp':False}}],
                'outbounds':[{
                    'protocol':'vless',
                    'settings':{'vnext':[{'address':'dedirock.enrpiglink.top','port':443,'users':[{'id':item['uuid'],'encryption':'none','flow':'xtls-rprx-vision'}]}]},
                    'streamSettings':{'network':'tcp','security':'reality','realitySettings':{'serverName':reality['server_name'],'fingerprint':'chrome','publicKey':reality['public_key'],'shortId':reality['short_id'],'spiderX':'/'}}
                }]
            }
            fd,config_path=tempfile.mkstemp(prefix='.sparklink-dual-',suffix='.json',dir='/run')
            os.close(fd)
            os.chmod(config_path,0o600)
            Path(config_path).write_text(json.dumps(client_config,separators=(',',':')),encoding='utf-8')
            if subprocess.run([BINARY,'run','-test','-config',config_path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45).returncode != 0:
                results.append({'user':user,'kind':kind,'ok':False,'error':'client_config_test_failed'})
                continue
            process=subprocess.Popen([BINARY,'run','-config',config_path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
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
                results.append({'user':user,'kind':kind,'ok':False,'error':'listener_not_ready'})
                continue
            trace=subprocess.run(['curl','--silent','--show-error','--max-time','25','--socks5-hostname',f'127.0.0.1:{port}','https://www.cloudflare.com/cdn-cgi/trace'],env=clean_env(),capture_output=True,text=True,timeout=35)
            warp=None
            for line in trace.stdout.splitlines():
                if line.startswith('warp='):
                    warp=line.split('=',1)[1]
                    break
            expected='on' if kind=='hytru' else 'off'
            results.append({'user':user,'kind':kind,'ok':trace.returncode==0 and warp==expected,'warp':warp,'curlExit':trace.returncode})
        except Exception:
            results.append({'user':user,'kind':kind,'ok':False,'error':'client_test_failed'})
        finally:
            stop(process)
            if config_path:
                try:
                    os.unlink(config_path)
                except OSError:
                    pass
result(all(item.get('ok') for item in results) and len(results)==len(expected_users)*2,
       tested=len(results),passed=sum(1 for item in results if item.get('ok')),
       origin_expected='off',hytru_expected='on',results=results)
'''


def _safe_discovery(value: dict) -> dict:
    managed = value.get("managed") or {}
    routes = value.get("route_tags") or {}
    return {
        "config_sha256": str(value.get("config_sha256") or ""),
        "config_mode": str(value.get("config_mode") or ""),
        "config_uid": int(value.get("config_uid", -1)),
        "config_gid": int(value.get("config_gid", -1)),
        "service_active": bool(value.get("service_active", True)),
        "config_test": bool(value.get("config_test", True)),
        "hytru_users": sorted((managed.get("hytru") or {}).keys()),
        "origin_users": sorted((managed.get("origin") or {}).keys()),
        "route_tags": {
            "hytru": sorted(set(
                tag for email, tags in routes.items()
                if HYTRU_EMAIL_RE.fullmatch(email) for tag in tags
            )),
            "origin": sorted(set(
                tag for email, tags in routes.items()
                if ORIGIN_EMAIL_RE.fullmatch(email) for tag in tags
            )),
        },
        "outbounds": value.get("outbounds", []),
        "xui_db_present": bool(value.get("xui_db_present")),
    }


def _eligible_users(users: list[dict]) -> list[dict]:
    by_name = {item.get("display_name"): item for item in users}
    if set(ELIGIBLE_USERNAMES) - set(by_name):
        raise DualRouteError("eligible_user_missing")
    result = []
    for username in ELIGIBLE_USERNAMES:
        user = by_name[username]
        if user.get("plan") not in {"Basic", "Plus"}:
            raise DualRouteError("eligible_user_plan_invalid")
        result.append(user)
    return result


def validate_discovery(value: dict, users: list[dict]) -> None:
    if not isinstance(value, dict):
        raise DualRouteError("discovery_invalid")
    for key in (
        "config_sha256", "managed", "route_tags", "reality", "outbounds", "all_client_ids"
    ):
        if key not in value:
            raise DualRouteError("discovery_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(value["config_sha256"]).lower()):
        raise DualRouteError("discovery_invalid")
    if value.get("xui_db_present"):
        raise DualRouteError("persistent_panel_requires_separate_migration")
    outbounds = value.get("outbounds") or []
    tags = {
        str(item.get("tag")): str(item.get("protocol", "")).lower()
        for item in outbounds if isinstance(item, dict)
    }
    if tags.get("direct") != "freedom" or tags.get("warp") != "wireguard":
        raise DualRouteError("required_outbounds_missing")
    expected_ids = {str(user["user_id"]) for user in users}
    managed = value.get("managed") or {}
    hytru = managed.get("hytru") or {}
    origin = managed.get("origin") or {}
    if set(hytru) != expected_ids or not set(origin).issubset(expected_ids):
        raise DualRouteError("managed_identity_set_invalid")
    for user_id, item in hytru.items():
        if (
            not isinstance(item, dict)
            or item.get("email") != managed_email(user_id)
            or not isinstance(item.get("uuid"), str)
            or not item["uuid"]
        ):
            raise DualRouteError("hytru_identity_invalid")
    for user_id, item in origin.items():
        if (
            not isinstance(item, dict)
            or item.get("email") != managed_email(user_id, "origin")
            or not isinstance(item.get("uuid"), str)
            or not item["uuid"]
        ):
            raise DualRouteError("origin_identity_invalid")
    if any(not isinstance(item, str) for item in value.get("all_client_ids", [])):
        raise DualRouteError("client_identity_inventory_invalid")


def build_plan(discovery: dict, users: list[dict]) -> dict:
    expected_users = _eligible_users(users)
    validate_discovery(discovery, expected_users)
    managed = discovery["managed"]
    origin_by_user = managed.get("origin") or {}
    used_ids = set(discovery["all_client_ids"])
    identities = []
    entries = []
    for user in expected_users:
        user_id = str(user["user_id"])
        email = managed_email(user_id, "origin")
        existing = origin_by_user.get(user_id)
        if existing is not None:
            client_uuid = str(existing["uuid"])
        else:
            client_uuid = str(uuid.uuid4())
            while client_uuid in used_ids:
                client_uuid = str(uuid.uuid4())
            used_ids.add(client_uuid)
        identities.append({
            "user_id": user_id,
            "email": email,
            "uuid": client_uuid,
            "source_email": managed_email(user_id),
            "new": existing is None,
        })
        entries.append({
            "user_id": user_id,
            "runtime_ref_hash": runtime_ref_hash(email),
            "runtime_family": "xray",
            "protocol": "vless",
            "credential_kind": "managed",
            "uri": admit_dedirock.build_vless_uri(
                client_uuid,
                discovery["reality"]["server_name"],
                discovery["reality"]["public_key"],
                discovery["reality"]["short_id"],
                route="Origin",
            ),
            "minimum_plan": "Basic",
        })
    return {
        "users": expected_users,
        "expected_user_ids": [str(user["user_id"]) for user in expected_users],
        "origin_identities": identities,
        "entries": entries,
    }


def inspect_live(ssh_host: str) -> dict:
    return _remote_json(ssh_host, REMOTE_INSPECT_SCRIPT)


def apply_live(ssh_host: str, discovery: dict, plan: dict) -> dict:
    value = _remote_json(ssh_host, REMOTE_APPLY_SCRIPT, {
        "expected_config_sha256": discovery["config_sha256"],
        "expected_user_ids": plan["expected_user_ids"],
        "origin_identities": plan["origin_identities"],
    })
    if value.get("route") != "dual":
        raise DualRouteError("remote_apply_response_invalid")
    return value


def acceptance(ssh_host: str) -> dict:
    value = _remote_json(ssh_host, REMOTE_ACCEPTANCE_SCRIPT)
    if int(value.get("tested", -1)) != 8 or int(value.get("passed", -1)) != 8:
        raise DualRouteError("dual_route_acceptance_count_mismatch")
    return value


def rollback_live(ssh_host: str, applied: dict) -> dict:
    backup = str(applied.get("backup") or "")
    if not backup.startswith("/var/backups/sparklink-identity-migration/"):
        raise DualRouteError("rollback_target_invalid")
    return _remote_json(ssh_host, REMOTE_ROLLBACK_SCRIPT, {
        "backup": backup,
        "expected_after_sha256": applied.get("after_sha256"),
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
        "source": SOURCE,
        "metering_status": "unknown",
        "supported_protocols": ["vless"],
        "detail": (
            "direct Reality VLESS ingress with separate native/direct and "
            "HyTru/WARP egress verified; DediRock per-user Stats unavailable; "
            "Usage Unknown; quota unavailable"
        ),
        "entries": entries,
    }
    with operator.selected_endpoint(cp_endpoint_args(args)) as endpoint:
        return operator.admin_json(
            endpoint, admin_token, "/api/admin/runtime-admission", "POST", payload
        )


def _detail_entries(detail: dict, plan: str, accessible_only: bool) -> list[dict]:
    access = {
        item["node_id"]: item
        for item in detail.get("effective_access", [])
        if isinstance(item, dict) and isinstance(item.get("node_id"), str)
    }
    result = []
    for entry in detail.get("subscription_entries", []):
        if (
            not isinstance(entry, dict)
            or not entry.get("enabled")
            or entry.get("projection_status") != "current"
            or str(entry.get("protocol", "")).lower() != "vless"
        ):
            continue
        minimum_plan = entry.get("minimum_plan")
        if minimum_plan not in PLAN_ORDER or PLAN_ORDER[plan] < PLAN_ORDER[minimum_plan]:
            continue
        node_id = entry.get("node_id")
        if accessible_only and node_id and access.get(node_id, {}).get("decision") != "allow":
            continue
        result.append(entry)
    return result


EXPECTED_NODE_ALIASES = {
    "hypro02": {
        "Pro-LA-02-Origin-Direct-Reality",
        "Pro-LA-02-HyTru-Direct-Reality",
    },
    "vmiss": {
        "Pro-LA-01-Origin-Direct-Reality",
        "Pro-LA-01-HyTru-Direct-Reality",
    },
    "racknerd": {
        "Standard-NY-Origin-Direct-Reality",
        "Standard-NY-HyTru-Direct-Reality",
    },
    "dedirock": {CANONICAL_DEDIROCK_ORIGIN_ALIAS, CANONICAL_DEDIROCK_HYTRU_ALIAS},
}


def verify_projection(args: argparse.Namespace) -> dict:
    admin_token = operator._admin_token(Path(args.secret_path))
    public_base = operator.validate_url(args.public_subscription_base_url)
    summary = {"users": 0, "portal_verified": 0, "public_verified": 0, "dual_nodes_verified": 0}
    with operator.selected_endpoint(cp_endpoint_args(args)) as endpoint:
        users = operator.read_admin_users(endpoint, admin_token)
        by_name = {item["display_name"]: item for item in users}
        if set(ELIGIBLE_USERNAMES) - set(by_name):
            raise DualRouteError("eligible_user_missing_after_admission")
        for user in sorted(users, key=lambda item: item["display_name"]):
            username = user["display_name"]
            bundle = operator.read_bundle(
                operator.user_bundle_path(username, Path(args.delivery_dir))
            )
            portal = bundle.get("portal_access_token") or bundle.get("portal_token")
            if not isinstance(portal, str) or not portal:
                raise DualRouteError("delivery_portal_missing")
            operator.verify_portal(endpoint, portal, user["user_id"])
            summary["portal_verified"] += 1
            detail = operator.admin_json(
                endpoint,
                admin_token,
                "/api/admin/users/" + operator.urllib.parse.quote(user["user_id"], safe=""),
                "GET",
            )
            all_entries = _detail_entries(detail, user["plan"], accessible_only=False)
            accessible_entries = _detail_entries(detail, user["plan"], accessible_only=True)
            for node_id in sorted({
                entry.get("node_id") for entry in accessible_entries if entry.get("node_id")
            }):
                if node_id not in EXPECTED_NODE_ALIASES:
                    continue
                aliases = {
                    str(entry.get("display_alias"))
                    for entry in accessible_entries
                    if entry.get("node_id") == node_id
                }
                if aliases != EXPECTED_NODE_ALIASES[node_id]:
                    raise DualRouteError("node_dual_route_projection_missing")
                summary["dual_nodes_verified"] += 1
            if user["subscription_status"] == "not_configured":
                if accessible_entries or all_entries:
                    raise DualRouteError("free_user_projection_invalid")
                continue
            subscription = bundle.get("subscription_url")
            if not isinstance(subscription, str):
                raise DualRouteError("delivery_subscription_missing")
            operator.verify_public_subscription_projection(
                subscription,
                user["plan"],
                user["subscription_status"],
                user["subscription_entry_count"],
                user["subscription_pool_ids"],
                user["subscription_protocols"],
                public_base,
            )
            status, raw = operator._request_url(
                subscription,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
            )
            if status != 200:
                raise DualRouteError("public_projection_fetch_failed")
            try:
                decoded = base64.b64decode(raw.strip(), validate=True).decode("utf-8")
            except Exception as exc:
                raise DualRouteError("public_projection_invalid") from exc
            lines = [line for line in decoded.splitlines() if line]
            if [alias_from_uri(line) for line in lines] != [
                entry["display_alias"] for entry in accessible_entries
            ]:
                raise DualRouteError("public_projection_alias_alignment_failed")
            summary["public_verified"] += 1
            summary["users"] += 1
    return summary


def reconcile_deliveries(args: argparse.Namespace) -> None:
    reconcile_args = SimpleNamespace(
        secret_path=Path(args.secret_path),
        delivery_dir=Path(args.delivery_dir),
        portal_url=args.portal_url,
        public_subscription_base_url=args.public_subscription_base_url,
        endpoint=None,
        ssh_host=args.control_plane_ssh_host,
        forward_port=args.control_plane_forward_port,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        if operator.reconcile(reconcile_args) != 0:
            raise DualRouteError("delivery_reconciliation_failed")


def _load_plan(args: argparse.Namespace) -> tuple[dict, dict]:
    discovery = inspect_live(args.dedirock_ssh_host)
    with operator.selected_endpoint(cp_endpoint_args(args)) as endpoint:
        users = operator.read_admin_users(
            endpoint, operator._admin_token(Path(args.secret_path))
        )
    return discovery, build_plan(discovery, users)


def preview(args: argparse.Namespace) -> dict:
    discovery, plan = _load_plan(args)
    identities = plan["origin_identities"]
    return {
        "ok": True,
        "command": "preview",
        "node_id": DEFAULT_NODE_ID,
        "route_variants": ["Origin(native)", "HyTru"],
        "existing_hytru_identities": len(discovery["managed"]["hytru"]),
        "existing_origin_identities": len(discovery["managed"]["origin"]),
        "origin_identities_to_add": sum(1 for item in identities if item["new"]),
        "control_plane_entries": len(plan["entries"]),
        "legacy_access_changed": False,
        "hard_quota": "disabled",
        "plaintext_not_printed": True,
    }


def run(args: argparse.Namespace) -> dict:
    discovery, plan = _load_plan(args)
    applied = apply_live(args.dedirock_ssh_host, discovery, plan)
    try:
        client_acceptance = acceptance(args.dedirock_ssh_host)
    except DualRouteError:
        if applied.get("changed") and applied.get("backup"):
            try:
                rollback_live(args.dedirock_ssh_host, applied)
            except DualRouteError as rollback_error:
                raise DualRouteError(
                    f"dual_route_acceptance_failed_rollback_failed_{rollback_error.code}"
                )
        raise
    try:
        cp_result = admit_control_plane(args, plan["entries"])
        reconcile_deliveries(args)
        verification = verify_projection(args)
    except (DualRouteError, operator.OperatorError) as exc:
        if applied.get("changed") and applied.get("backup"):
            try:
                rollback_live(args.dedirock_ssh_host, applied)
            except DualRouteError as rollback_error:
                code = getattr(exc, "code", "control_plane_or_delivery_failed")
                raise DualRouteError(f"{code}_rollback_failed_{rollback_error.code}")
        if isinstance(exc, DualRouteError):
            raise
        raise DualRouteError(getattr(exc, "code", "control_plane_or_delivery_failed")) from exc
    return {
        "ok": True,
        "node_id": DEFAULT_NODE_ID,
        "route_variants": ["Origin(native)", "HyTru"],
        "runtime_origin_added": int(applied.get("origin_added", 0)),
        "runtime_managed_identities": int(applied.get("managed_count", 0)),
        "client_acceptance": {
            "tested": int(client_acceptance["tested"]),
            "passed": int(client_acceptance["passed"]),
            "origin_warp": "off",
            "hytru_warp": "on",
        },
        "control_plane": {
            "credentials_created": cp_result.get("credentials_created"),
            "credentials_reused": cp_result.get("credentials_reused"),
            "subscriptions_created": cp_result.get("subscriptions_created"),
            "subscriptions_reused": cp_result.get("subscriptions_reused"),
            "metering_status": cp_result.get("metering_status"),
            "quota_status": cp_result.get("quota_status"),
        },
        "projection": verification,
        "rollback_backup": applied.get("backup") or None,
        "legacy_access_changed": False,
        "hard_quota": "disabled",
        "plaintext_not_printed": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ensure DediRock Advanced has separate Origin and HyTru routes"
    )
    parser.add_argument("command", choices=("preview", "apply"), default="preview", nargs="?")
    parser.add_argument("--dedirock-ssh-host", default=DEFAULT_SSH_HOST)
    parser.add_argument("--control-plane-ssh-host", default=DEFAULT_CONTROL_PLANE_SSH_HOST)
    parser.add_argument("--control-plane-forward-port", type=int, default=DEFAULT_CONTROL_PLANE_FORWARD_PORT)
    parser.add_argument("--secret-path", type=Path, default=DEFAULT_SECRET_PATH)
    parser.add_argument("--delivery-dir", type=Path, default=DEFAULT_DELIVERY_DIR)
    parser.add_argument("--portal-url", default="https://spark.enrpiglink.top")
    parser.add_argument("--public-subscription-base-url", default=DEFAULT_PUBLIC_SUBSCRIPTION_BASE_URL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        value = preview(args) if args.command == "preview" else run(args)
        print(json.dumps(value, separators=(",", ":")))
        return 0
    except (DualRouteError, operator.OperatorError) as exc:
        print(json.dumps({
            "ok": False,
            "error": getattr(exc, "code", str(exc)),
            "plaintext_not_printed": True,
        }, separators=(",", ":")))
        return 1
    except Exception:
        print(json.dumps({
            "ok": False,
            "error": "dedirock_dual_route_failed",
            "plaintext_not_printed": True,
        }, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
