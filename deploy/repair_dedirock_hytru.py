#!/usr/bin/env python3
"""Repair the managed DediRock Advanced HyTru route.

This local OWNER/operator workflow adds one exact-user Xray routing rule for the
currently managed sparklink:<user>:advanced identities so they use the
existing warp WireGuard outbound. UUIDs, keys, tokens, and subscription
URIs stay in process memory or transient remote files and are never printed or
stored in the repository. A failed public acceptance restores the root-only
backup automatically.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path


DEFAULT_SSH_HOST = "dedirock-admin"
MANAGED_EMAIL_RE = re.compile(r"^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced$")


class HyTruRepairError(RuntimeError):
    """A non-secret operator-facing failure."""


def _managed_emails(config: dict) -> list[str]:
    values: set[str] = set()
    for inbound in config.get("inbounds", []):
        settings = inbound.get("settings") or {}
        clients = settings.get("clients") or []
        if not isinstance(clients, list):
            continue
        for client in clients:
            if not isinstance(client, dict):
                continue
            email = str(client.get("email") or "")
            if MANAGED_EMAIL_RE.fullmatch(email):
                values.add(email)
    if not values:
        raise HyTruRepairError("managed_advanced_identities_missing")
    return sorted(values)


def _validate_route_shape(config: dict, managed_emails: list[str]) -> None:
    if not isinstance(config.get("routing"), dict):
        raise HyTruRepairError("routing_section_missing")
    if not isinstance(config["routing"].get("rules"), list):
        raise HyTruRepairError("routing_rules_missing")
    outbounds = config.get("outbounds")
    if not isinstance(outbounds, list):
        raise HyTruRepairError("outbounds_missing")
    warp = next((item for item in outbounds
                 if isinstance(item, dict) and item.get("tag") == "warp"), None)
    if not isinstance(warp, dict) or str(warp.get("protocol", "")).lower() != "wireguard":
        raise HyTruRepairError("warp_wireguard_outbound_missing")
    if any(not MANAGED_EMAIL_RE.fullmatch(email) for email in managed_emails):
        raise HyTruRepairError("managed_identity_shape_invalid")


def build_hytru_routing(config: dict, managed_emails: list[str]) -> tuple[dict, bool]:
    """Return a candidate config with one exact managed-user -> warp rule."""

    _validate_route_shape(config, managed_emails)
    managed = set(managed_emails)
    rules = config["routing"]["rules"]
    exact_matches = 0
    selector_occurrences = 0
    for rule in rules:
        if not isinstance(rule, dict):
            raise HyTruRepairError("routing_rule_invalid")
        selectors = rule.get("user")
        if not isinstance(selectors, list):
            continue
        overlap = managed.intersection(str(value) for value in selectors)
        selector_occurrences += len(overlap)
        if (overlap == managed
                and set(str(value) for value in selectors) == managed
                and rule.get("type") == "field"
                and rule.get("outboundTag") == "warp"
                and set(rule).issubset({"type", "user", "outboundTag"})):
            exact_matches += 1
    if exact_matches == 1 and selector_occurrences == len(managed):
        return copy.deepcopy(config), False

    cleaned: list[dict] = []
    for rule in rules:
        selectors = rule.get("user")
        if not isinstance(selectors, list):
            cleaned.append(copy.deepcopy(rule))
            continue
        overlap = managed.intersection(str(value) for value in selectors)
        if not overlap:
            cleaned.append(copy.deepcopy(rule))
            continue
        if (rule.get("type") != "field"
                or not set(rule).issubset({"type", "user", "outboundTag"})):
            raise HyTruRepairError("managed_route_rule_shape_ambiguous")
        remaining = [str(value) for value in selectors if str(value) not in managed]
        if remaining:
            updated = copy.deepcopy(rule)
            updated["user"] = remaining
            cleaned.append(updated)

    insert_at = len(cleaned)
    for index, rule in enumerate(cleaned):
        if isinstance(rule.get("user"), list) and rule["user"]:
            insert_at = index
            break
    cleaned.insert(insert_at, {
        "type": "field",
        "user": sorted(managed),
        "outboundTag": "warp",
    })
    candidate = copy.deepcopy(config)
    candidate["routing"]["rules"] = cleaned

    observed: dict[str, list[str]] = defaultdict(list)
    for rule in cleaned:
        if not isinstance(rule, dict) or not isinstance(rule.get("user"), list):
            continue
        for value in rule["user"]:
            if str(value) in managed:
                observed[str(value)].append(str(rule.get("outboundTag", "")))
    if set(observed) != managed or any(tags != ["warp"] for tags in observed.values()):
        raise HyTruRepairError("managed_hytru_route_verification_failed")
    return candidate, True


def _remote_json(source: str, payload: dict | None = None) -> dict:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    payload_text = json.dumps(payload or {}, separators=(",", ":"))
    wrapper = (
        "import base64,io,json,sys\n"
        "sys.stdin=io.StringIO(" + repr(payload_text) + ")\n"
        "exec(compile(base64.b64decode(" + repr(encoded)
        + "),'<sparklink-hytru-repair>','exec'))\n"
    )
    try:
        result = subprocess.run(
            ["ssh.exe", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
             DEFAULT_SSH_HOST, "sudo -n python3 -"],
            input=wrapper.encode("utf-8"), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=180, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HyTruRepairError("remote_ssh_failed") from exc
    lines = [line.strip() for line in result.stdout.decode(errors="replace").splitlines()
             if line.strip().startswith("{")]
    # A remote service restart can close the SSH transport after the remote
    # script has already emitted its structured result.  The caller still
    # requires the independent postcondition acceptance before success.
    if not lines:
        raise HyTruRepairError("remote_response_invalid")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise HyTruRepairError("remote_response_invalid") from exc
    if not isinstance(value, dict) or value.get("ok") is not True:
        code = str(value.get("error") or "remote_operation_failed") if isinstance(value, dict) else "remote_operation_failed"
        suffix = "_rolled_back" if isinstance(value, dict) and value.get("rolled_back") is True else ""
        raise HyTruRepairError(code + suffix)
    return value


REMOTE_INSPECT_SCRIPT = r'''
import hashlib,json,re,subprocess
from pathlib import Path

CONFIG=Path('/etc/xray/config.json')
MANAGED_RE=re.compile(r'^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced$')

def fail(code):
    print(json.dumps({'ok':False,'error':code},separators=(',',':')))
    raise SystemExit(0)

def sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()

if not CONFIG.is_file():
    fail('config_missing')
try:
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
except Exception:
    fail('config_invalid')
managed=[]
for inbound in config.get('inbounds',[]):
    clients=(inbound.get('settings') or {}).get('clients') or []
    for client in clients:
        if not isinstance(client,dict):
            continue
        email=str(client.get('email') or '')
        match=MANAGED_RE.fullmatch(email)
        if match:
            managed.append((email,match.group('user')))
if not managed:
    fail('managed_advanced_identities_missing')
routes={email:set() for email,_ in managed}
for rule in (config.get('routing') or {}).get('rules',[]):
    selectors=rule.get('user',[]) if isinstance(rule,dict) else []
    if not isinstance(selectors,list):
        continue
    tag=str(rule.get('outboundTag',''))
    for email,_ in managed:
        if email in selectors:
            routes[email].add(tag)
warp=next((item for item in config.get('outbounds',[]) if isinstance(item,dict) and item.get('tag')=='warp'),None)
test=subprocess.run(['/usr/local/bin/xray','run','-test','-config',str(CONFIG)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45)
print(json.dumps({
    'ok':True,
    'config_sha256':sha256(CONFIG),
    'service_active':subprocess.run(['systemctl','is-active','--quiet','xray']).returncode==0,
    'config_test':test.returncode==0,
    'managed_users':sorted(set(user for _,user in managed)),
    'managed_count':len(managed),
    'explicit_route_tags':sorted(set(tag for values in routes.values() for tag in values)),
    'per_user_route_tags':{user:sorted(routes[email]) for email,user in managed},
    'warp_wireguard':isinstance(warp,dict) and str(warp.get('protocol','')).lower()=='wireguard',
    'outbound_order':[str(item.get('tag','')) for item in config.get('outbounds',[]) if isinstance(item,dict)]
},separators=(',',':')))
'''


REMOTE_APPLY_SCRIPT = r'''
import copy,datetime,hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path

CONFIG=Path('/etc/xray/config.json')
SERVICE='xray'
BACKUP_ROOT=Path('/var/backups/sparklink-identity-migration')
MANAGED_RE=__import__('re').compile(r'^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced$')

def result(ok,**values):
    print(json.dumps({'ok':ok,**values},separators=(',',':')))
    raise SystemExit(0)

def sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()

def run(command,timeout=45):
    return subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)

def restore(backup,source_stat,binary):
    shutil.copy2(backup,CONFIG)
    os.chmod(CONFIG,source_stat.st_mode & 0o7777)
    os.chown(CONFIG,source_stat.st_uid,source_stat.st_gid)
    if run([binary,'run','-test','-config',str(CONFIG)]).returncode != 0:
        raise RuntimeError('rollback_config_test_failed')
    if run(['systemctl','restart',SERVICE],60).returncode != 0 or run(['systemctl','is-active','--quiet',SERVICE],15).returncode != 0:
        raise RuntimeError('rollback_service_failed')

payload=json.load(sys.stdin)
expected_sha=str(payload.get('expected_config_sha256') or '').lower()
expected_users=payload.get('expected_managed_users')
if (not __import__('re').fullmatch(r'[0-9a-f]{64}',expected_sha)
        or not isinstance(expected_users,list) or not expected_users
        or any(not isinstance(value,str) for value in expected_users)):
    result(False,error='apply_payload_invalid')
if not CONFIG.is_file():
    result(False,error='config_missing')
try:
    config=json.loads(CONFIG.read_text(encoding='utf-8'))
except Exception:
    result(False,error='config_invalid')
if sha256(CONFIG).lower()!=expected_sha:
    result(False,error='config_changed_since_inspect')
managed=[]
for inbound in config.get('inbounds',[]):
    clients=(inbound.get('settings') or {}).get('clients') or []
    for client in clients:
        if not isinstance(client,dict):
            continue
        email=str(client.get('email') or '')
        match=MANAGED_RE.fullmatch(email)
        if match:
            managed.append((email,match.group('user')))
users=sorted(set(user for _,user in managed))
if (len(managed)!=len(expected_users) or users!=sorted(set(expected_users))
        or len(managed)!=len(set(email for email,_ in managed))):
    result(False,error='managed_identity_set_changed')
warp=next((item for item in config.get('outbounds',[]) if isinstance(item,dict) and item.get('tag')=='warp'),None)
if not isinstance(warp,dict) or str(warp.get('protocol','')).lower()!='wireguard':
    result(False,error='warp_wireguard_outbound_missing')
routing=config.get('routing')
if not isinstance(routing,dict) or not isinstance(routing.get('rules'),list):
    result(False,error='routing_rules_missing')
managed_set=set(email for email,_ in managed)
rules=routing['rules']
exact=0
occurrences=0
for rule in rules:
    if not isinstance(rule,dict):
        result(False,error='routing_rule_invalid')
    selectors=rule.get('user')
    if not isinstance(selectors,list):
        continue
    overlap=managed_set.intersection(str(value) for value in selectors)
    occurrences += len(overlap)
    if (overlap==managed_set and set(str(value) for value in selectors)==managed_set
            and rule.get('type')=='field' and rule.get('outboundTag')=='warp'
            and set(rule).issubset({'type','user','outboundTag'})):
        exact += 1
if exact==1 and occurrences==len(managed_set):
    result(True,changed=False,backup='',before_sha256=expected_sha,after_sha256=expected_sha,managed_count=len(managed),managed_users=users,route='warp')
cleaned=[]
for rule in rules:
    selectors=rule.get('user')
    if not isinstance(selectors,list):
        cleaned.append(copy.deepcopy(rule))
        continue
    overlap=managed_set.intersection(str(value) for value in selectors)
    if not overlap:
        cleaned.append(copy.deepcopy(rule))
        continue
    if rule.get('type')!='field' or not set(rule).issubset({'type','user','outboundTag'}):
        result(False,error='managed_route_rule_shape_ambiguous')
    remaining=[str(value) for value in selectors if str(value) not in managed_set]
    if remaining:
        updated=copy.deepcopy(rule)
        updated['user']=remaining
        cleaned.append(updated)
insert_at=len(cleaned)
for index,rule in enumerate(cleaned):
    if isinstance(rule.get('user'),list) and rule['user']:
        insert_at=index
        break
cleaned.insert(insert_at,{'type':'field','user':sorted(managed_set),'outboundTag':'warp'})
candidate=copy.deepcopy(config)
candidate['routing']['rules']=cleaned
observed={email:[] for email in managed_set}
for rule in cleaned:
    if not isinstance(rule,dict) or not isinstance(rule.get('user'),list):
        continue
    for value in rule['user']:
        if str(value) in observed:
            observed[str(value)].append(str(rule.get('outboundTag','')))
if set(observed)!=managed_set or any(tags!=['warp'] for tags in observed.values()):
    result(False,error='managed_hytru_route_verification_failed')
binary='/usr/local/bin/xray'
source_stat=CONFIG.stat()
backup=None
try:
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_dir=BACKUP_ROOT/(stamp+'-dedirock-hytru-route')
    backup_dir.mkdir(mode=0o700,parents=True,exist_ok=False)
    os.chmod(backup_dir,0o700)
    backup=backup_dir/'xray-config.json'
    shutil.copy2(CONFIG,backup)
    os.chmod(backup,0o600)
    os.chown(backup,0,0)
    if sha256(backup).lower()!=expected_sha:
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
    if run([binary,'run','-test','-config',str(CONFIG)]).returncode!=0:
        raise RuntimeError('config_test_failed')
    if run(['systemctl','restart',SERVICE],60).returncode!=0 or run(['systemctl','is-active','--quiet',SERVICE],15).returncode!=0:
        raise RuntimeError('service_restart_failed')
    result(True,changed=True,backup=str(backup),before_sha256=expected_sha,after_sha256=sha256(CONFIG),managed_count=len(managed),managed_users=users,route='warp')
except Exception:
    rolled_back=False
    if backup is not None:
        try:
            restore(backup,source_stat,binary)
            rolled_back=True
        except Exception:
            pass
    result(False,error='hytru_apply_failed',rolled_back=rolled_back)
'''


REMOTE_ROLLBACK_SCRIPT = r'''
import hashlib,json,os,re,shutil,subprocess,sys
from pathlib import Path

CONFIG=Path('/etc/xray/config.json')
ROOT=Path('/var/backups/sparklink-identity-migration')

def result(ok,**values):
    print(json.dumps({'ok':ok,**values},separators=(',',':')))
    raise SystemExit(0)

def sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()

def run(command,timeout=60):
    return subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)

payload=json.load(sys.stdin)
backup=Path(str(payload.get('backup') or ''))
expected=str(payload.get('expected_after_sha256') or '').lower()
if (not str(backup).startswith(str(ROOT)+'/') or backup.name!='xray-config.json'
        or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-dedirock-hytru-route',backup.parent.name)
        or not backup.is_file() or not CONFIG.is_file()
        or not re.fullmatch(r'[0-9a-f]{64}',expected)):
    result(False,error='rollback_target_invalid')
if sha256(CONFIG).lower()!=expected:
    result(False,error='rollback_config_changed')
source_stat=CONFIG.stat()
try:
    shutil.copy2(backup,CONFIG)
    os.chmod(CONFIG,source_stat.st_mode & 0o7777)
    os.chown(CONFIG,source_stat.st_uid,source_stat.st_gid)
    if run(['/usr/local/bin/xray','run','-test','-config',str(CONFIG)],45).returncode!=0:
        result(False,error='rollback_config_test_failed')
    if run(['systemctl','restart','xray'],60).returncode!=0 or run(['systemctl','is-active','--quiet','xray'],15).returncode!=0:
        result(False,error='rollback_service_failed')
    result(True,restored=True,config_sha256=sha256(CONFIG))
except Exception:
    result(False,error='rollback_failed')
'''


REMOTE_ACCEPTANCE_SCRIPT = r'''
import json,os,re,socket,subprocess,tempfile,time
from pathlib import Path

CONFIG=Path('/etc/xray/config.json')
MANAGED_RE=re.compile(r'^sparklink:(?P<user>[A-Za-z0-9_.-]+):advanced$')

def result(ok,**values):
    print(json.dumps({'ok':ok,**values},separators=(',',':')))
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

def public_key(binary,private_key):
    value=subprocess.run([binary,'x25519','-i',private_key],capture_output=True,text=True,timeout=30)
    if value.returncode!=0:
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
binary='/usr/local/bin/xray'
reality=None
managed=[]
for inbound in config.get('inbounds',[]):
    settings=inbound.get('settings') or {}
    clients=settings.get('clients') or []
    for client in clients:
        if not isinstance(client,dict):
            continue
        email=str(client.get('email') or '')
        match=MANAGED_RE.fullmatch(email)
        if match:
            managed.append({'user':match.group('user'),'uuid':str(client.get('id') or '')})
    stream=inbound.get('streamSettings') or {}
    reality_settings=stream.get('realitySettings') or {}
    if (str(inbound.get('protocol','')).lower()=='vless' and int(inbound.get('port',0) or 0)==443
            and str(stream.get('security','')).lower()=='reality'):
        reality={'server_name':str((reality_settings.get('serverNames') or [''])[0]),'short_id':str((reality_settings.get('shortIds') or [''])[0]),'private_key':str(reality_settings.get('privateKey') or '')}
if not reality or not managed or not all(item['uuid'] for item in managed):
    result(False,error='acceptance_fixture_missing')
pbk=public_key(binary,reality['private_key'])
if not pbk or not reality['server_name'] or not reality['short_id']:
    result(False,error='reality_public_parameters_unavailable')
results=[]
for item in sorted(managed,key=lambda x:x['user']):
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.bind(('127.0.0.1',0))
    port=listener.getsockname()[1]
    listener.close()
    config_path=None
    process=None
    try:
        client_config={'log':{'loglevel':'none'},'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks','settings':{'auth':'noauth','udp':False}}],'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':'dedirock.enrpiglink.top','port':443,'users':[{'id':item['uuid'],'encryption':'none','flow':'xtls-rprx-vision'}]}]},'streamSettings':{'network':'tcp','security':'reality','realitySettings':{'serverName':reality['server_name'],'fingerprint':'chrome','publicKey':pbk,'shortId':reality['short_id'],'spiderX':'/'}}}]}
        fd,config_path=tempfile.mkstemp(prefix='.sparklink-hytru-',suffix='.json',dir='/run')
        os.close(fd)
        os.chmod(config_path,0o600)
        Path(config_path).write_text(json.dumps(client_config,separators=(',',':')),encoding='utf-8')
        if subprocess.run([binary,'run','-test','-config',config_path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45).returncode!=0:
            results.append({'user':item['user'],'ok':False,'error':'client_config_test_failed'})
            continue
        process=subprocess.Popen([binary,'run','-config',config_path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
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
            results.append({'user':item['user'],'ok':False,'error':'listener_not_ready'})
            continue
        trace=subprocess.run(['curl','--silent','--show-error','--max-time','25','--socks5-hostname',f'127.0.0.1:{port}','https://www.cloudflare.com/cdn-cgi/trace'],env=clean_env(),capture_output=True,text=True,timeout=35)
        warp=None
        for line in trace.stdout.splitlines():
            if line.startswith('warp='):
                warp=line.split('=',1)[1]
                break
        results.append({'user':item['user'],'ok':trace.returncode==0 and warp=='on','warp':warp,'curlExit':trace.returncode})
    except Exception:
        results.append({'user':item['user'],'ok':False,'error':'client_test_failed'})
    finally:
        stop(process)
        if config_path:
            try:
                os.unlink(config_path)
            except OSError:
                pass
result(all(item.get('ok') for item in results) and len(results)==len(managed),tested=len(results),passed=sum(1 for item in results if item.get('ok')),results=results)
'''


def _safe_inspect(value: dict) -> dict:
    return {
        "managed_count": int(value.get("managed_count", 0)),
        "managed_users": sorted(str(item) for item in value.get("managed_users", [])),
        "explicit_route_tags": sorted(str(item) for item in value.get("explicit_route_tags", [])),
        "per_user_route_tags": {
            str(user): sorted(str(tag) for tag in tags)
            for user, tags in (value.get("per_user_route_tags") or {}).items()
        },
        "warp_wireguard": bool(value.get("warp_wireguard")),
        "service_active": bool(value.get("service_active")),
        "config_test": bool(value.get("config_test")),
        "outbound_order": [str(item) for item in value.get("outbound_order", [])],
    }


def inspect_live() -> dict:
    return _remote_json(REMOTE_INSPECT_SCRIPT)


def apply_live(before: dict) -> dict:
    users = sorted(set(str(item) for item in before.get("managed_users", [])))
    if not users or not before.get("config_sha256"):
        raise HyTruRepairError("inspect_result_incomplete")
    return _remote_json(REMOTE_APPLY_SCRIPT, {
        "expected_config_sha256": str(before["config_sha256"]),
        "expected_managed_users": users,
    })


def rollback_live(applied: dict) -> dict:
    return _remote_json(REMOTE_ROLLBACK_SCRIPT, {
        "backup": applied.get("backup"),
        "expected_after_sha256": applied.get("after_sha256"),
    })


def run(command: str) -> int:
    try:
        before = inspect_live()
        safe_before = _safe_inspect(before)
        if command == "preview":
            needs_change = not (
                safe_before["managed_count"] == len(safe_before["managed_users"])
                and safe_before["managed_count"] > 0
                and all(tags == ["warp"]
                        for tags in safe_before["per_user_route_tags"].values())
            )
            print(json.dumps({
                "ok": True,
                "command": "preview",
                "before": safe_before,
                "would_add_managed_warp_rule": needs_change,
                "plaintext_not_printed": True,
            }, ensure_ascii=False, separators=(",", ":")))
            return 0
        if (not before.get("service_active") or not before.get("config_test")
                or not before.get("warp_wireguard")
                or int(before.get("managed_count", 0)) != 4
                or len(before.get("managed_users", [])) != 4):
            raise HyTruRepairError("live_precondition_failed")
        applied = apply_live(before)
        try:
            acceptance = _remote_json(REMOTE_ACCEPTANCE_SCRIPT)
        except HyTruRepairError as exc:
            rollback = None
            rollback_error = None
            if applied.get("changed") and applied.get("backup"):
                try:
                    rollback = rollback_live(applied)
                except HyTruRepairError as rollback_exc:
                    rollback_error = str(rollback_exc)
            print(json.dumps({
                "ok": False,
                "command": "apply",
                "error": str(exc),
                "applied": {"changed": bool(applied.get("changed")), "backup": applied.get("backup")},
                "acceptance": "failed",
                "rollback": "passed" if rollback else ("not_needed" if not applied.get("changed") else "failed"),
                "rollback_error": rollback_error,
                "plaintext_not_printed": True,
            }, ensure_ascii=False, separators=(",", ":")))
            return 1
        print(json.dumps({
            "ok": True,
            "command": "apply",
            "before": safe_before,
            "applied": {
                "changed": bool(applied.get("changed")),
                "backup": applied.get("backup") or None,
                "managed_count": int(applied.get("managed_count", 0)),
                "route": "warp",
            },
            "acceptance": {
                "tested": int(acceptance.get("tested", 0)),
                "passed": int(acceptance.get("passed", 0)),
                "warp": "on",
            },
            "plaintext_not_printed": True,
        }, ensure_ascii=False, separators=(",", ":")))
        return 0
    except HyTruRepairError as exc:
        print(json.dumps({
            "ok": False, "command": command, "error": str(exc),
            "plaintext_not_printed": True,
        }, ensure_ascii=False, separators=(",", ":")))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair DediRock managed Advanced HyTru routing")
    parser.add_argument("command", choices=("preview", "apply"), nargs="?", default="preview")
    return run(parser.parse_args().command)


if __name__ == "__main__":
    raise SystemExit(main())
