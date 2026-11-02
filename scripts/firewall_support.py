"""Conservative local-firewall detection and explicit changes."""
from __future__ import annotations
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess

def run(args):
    return subprocess.run(args,capture_output=True,text=True)

def active_firewalld_zone(output: str) -> str | None:
    """Only one interface-bound zone with no source-specific routing is safe.

    The default zone is not evidence of where inbound traffic arrives. Multiple
    active zones, source bindings and unknown output need operator knowledge;
    never guess which interface/source carries the user's public traffic.
    """
    zones = {}
    current = None
    for line in output.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            name = line.strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in zones:
                return None
            current = name
            zones[current] = set()
        else:
            match = re.fullmatch(r"\s+(interfaces|sources):\s*(.*)", line)
            if current is None or match is None:
                return None
            kind, values = match.groups()
            if kind in zones[current]:
                return None
            if kind == "sources" and values.strip():
                return None
            if kind == "interfaces" and values.strip():
                zones[current].add(kind)
    if len(zones) != 1:
        return None
    zone, bindings = next(iter(zones.items()))
    return zone if "interfaces" in bindings else None


def detect() -> dict:
    ufw=shutil.which("ufw")
    if ufw:
        status=run([ufw,"status"])
        if status.returncode==0 and re.search(r"^Status:\s+active\s*$",status.stdout,re.M|re.I):
            return {"backend":"ufw","managed":True,"detail":"UFW active"}
    firewall=shutil.which("firewall-cmd")
    if firewall:
        state=run([firewall,"--state"])
        if state.returncode==0 and state.stdout.strip()=="running":
            active=run([firewall,"--get-active-zones"])
            zone=active_firewalld_zone(active.stdout) if active.returncode==0 else None
            if zone is None:
                return {"backend":"firewalld","managed":False,
                        "detail":"firewalld ingress zone is ambiguous or unavailable; manual configuration required"}
            return {"backend":"firewalld","managed":True,
                    "detail":"firewalld active interface zone: "+zone,"zone":zone}
    nft=shutil.which("nft")
    if nft:
        rules=run([nft,'-j','list','ruleset'])
        if rules.returncode:
            return {'backend':'nftables','managed':False,'detail':'nftables rules unavailable'}
        if rules.stdout.strip():
            try:
                if len(rules.stdout)>1_000_000:raise ValueError()
                snapshot=json.loads(rules.stdout)
                entries=snapshot['nftables']
                if type(entries) is not list or len(entries)>4096:raise ValueError()
                if any(type(entry) is not dict or set(entry)!={'metainfo'} for entry in entries):
                    parsed=nft_input_rules(entries)
                    return {'backend':'iptables-nft' if parsed is not None else 'nftables',
                            'managed':False,'detail':'custom nftables rules detected',
                            'input_rules':parsed,'ipv6_disabled':ipv6_disabled()}
            except (ValueError,KeyError,TypeError):
                return {"backend":"nftables","managed":False,"detail":"custom nftables rules detected"}
    iptables=shutil.which("iptables-save")
    if iptables:
        rules=run([iptables])
        if rules.returncode:
            return {'backend':'iptables','managed':False,'detail':'iptables rules unavailable'}
        if rules.stdout.strip():
            return {"backend":"iptables","managed":False,"detail":"custom iptables rules detected",
                    'input_rules':iptables_input_rules(rules.stdout),'ipv6_disabled':ipv6_disabled()}
    return {"backend":"none","managed":False,"detail":"no active supported local firewall detected"}


def ipv6_disabled():
    try:
        return Path('/proc/sys/net/ipv6/conf/all/disable_ipv6').read_text().strip()=='1'
    except OSError:
        return False


def iptables_input_rules(output):
    """Only the simple IPv4 filter table; jumps/extra tables remain unknown."""
    if len(output)>262144:return None
    chains={};rules=[];table=False;committed=False
    try:
        for count,line in enumerate(output.splitlines()):
            if count>4096:return None
            if not line or line.startswith('#'):continue
            if line=='*filter' and not table and not committed:table=True;continue
            if line=='COMMIT' and table:table=False;committed=True;continue
            if not table:return None
            if line.startswith(':'):
                match=re.fullmatch(r':(INPUT|FORWARD|OUTPUT) (ACCEPT|DROP) \[\d+:\d+\]',line)
                if not match or match[1] in chains:return None
                chains[match[1]]=match[2];continue
            tokens=shlex.split(line)
            if tokens[:2]!=['-A','INPUT']:return None
            condition={};action=None;i=2
            while i<len(tokens):
                key=tokens[i];value=tokens[i+1];i+=2
                if key=='-m' and value in ('tcp','udp','state','conntrack','comment'):continue
                if key in ('--comment','--reject-with'):continue
                if key in ('-p','--protocol'):
                    if 'protocol' in condition:return None
                    condition['protocol']=value
                elif key in ('--dport','--destination-port'):
                    if not value.isdecimal() or not 1<=int(value)<=65535:return None
                    if 'port' in condition:return None
                    condition['port']=int(value)
                elif key in ('--ctstate','--state'):
                    states=set(value.split(','))
                    if not states or not states<={'NEW','ESTABLISHED','RELATED','INVALID','UNTRACKED'}:return None
                    if 'states' in condition:return None
                    condition['states']=states
                elif key in ('-i','--in-interface'):
                    if 'interface' in condition:return None
                    condition['interface']=value
                elif key in ('-s','--source'):
                    if value!='0.0.0.0/0':return None
                elif key in ('-d','--destination'):
                    if value!='0.0.0.0/0':return None
                elif key in ('-j','--jump') and value in ('ACCEPT','DROP','REJECT') and action is None:action=value
                else:return None
            if action is None:return None
            rules.append((condition,action))
    except (ValueError,IndexError):return None
    if not committed or table or set(chains)!={'INPUT','OUTPUT','FORWARD'} or chains['OUTPUT']!='ACCEPT':return None
    return chains['INPUT'],rules


def nft_input_rules(entries):
    """Recognize one ordinary ip/filter table from iptables-nft JSON only.

    Native extra tables, IPv6, sets, custom chains and expressions cannot be
    inferred from an iptables-save subset and are deliberately unknown.
    """
    chains={};rules=[];table=False
    try:
        for entry in entries:
            if type(entry) is not dict or len(entry)!=1:return None
            kind,row=next(iter(entry.items()))
            if kind=='metainfo':continue
            if type(row) is not dict or row.get('family')!='ip':return None
            if kind=='table':
                if table or row.get('name')!='filter' or set(row)-{'family','name','handle'}:return None
                table=True;continue
            if row.get('table')!='filter':return None
            if kind=='chain':
                name=row.get('name');hook={'INPUT':'input','OUTPUT':'output','FORWARD':'forward'}.get(name)
                if (not hook or name in chains or row.get('hook')!=hook or row.get('type')!='filter'
                        or row.get('prio')!=0 or row.get('policy') not in ('accept','drop')
                        or set(row)-{'family','table','name','handle','type','hook','prio','policy'}):return None
                chains[name]=row['policy'].upper();continue
            if kind!='rule' or row.get('chain')!='INPUT' or type(row.get('expr')) is not list:return None
            condition={};action=None
            for expression in row['expr']:
                if type(expression) is not dict or len(expression)!=1:return None
                key,value=next(iter(expression.items()))
                if action is not None:return None
                if key=='counter':continue
                if key in ('accept','drop','reject'):
                    if action is not None:return None
                    action=key.upper();continue
                if key!='match':return None
                left=value['left'];right=value['right']
                if (value.get('op')!='==' and not (value.get('op')=='in'
                        and left=={'ct':{'key':'state'}} and type(right) is dict and set(right)=={'set'})):return None
                if left=={'meta':{'key':'l4proto'}}:
                    if right not in ('tcp','udp','icmp'):return None
                    if condition.get('protocol',right)!=right:return None
                    condition['protocol']=right
                elif left=={'meta':{'key':'iifname'}}:
                    if type(right) is not str:return None
                    if 'interface' in condition:return None
                    condition['interface']=right
                elif left=={'ct':{'key':'state'}}:
                    states=right.get('set') if type(right) is dict else [right]
                    if type(states) is not list or any(state not in ('new','related','established','invalid','untracked') for state in states):return None
                    if 'states' in condition:return None
                    condition['states']={state.upper() for state in states}
                elif type(left) is dict and set(left)=={'payload'}:
                    payload=left['payload']
                    if (type(payload) is not dict or payload.get('protocol') not in ('tcp','udp')
                            or payload.get('field')!='dport' or type(right) is not int or not 1<=right<=65535):return None
                    if condition.get('protocol',payload['protocol'])!=payload['protocol']:return None
                    if 'port' in condition:return None
                    condition.update(protocol=payload['protocol'],port=right)
                else:return None
            if action is None:return None
            rules.append((condition,action))
    except (KeyError,TypeError,AttributeError):return None
    if not table or set(chains)!={'INPUT','OUTPUT','FORWARD'} or chains['OUTPUT']!='ACCEPT':return None
    return chains['INPUT'],rules


def input_port_open(parsed, port, protocol):
    if parsed is None:return None
    policy,rules=parsed
    for condition,action in rules:
        if condition.get('protocol',protocol)!=protocol or condition.get('port',port)!=port:continue
        if 'states' in condition and 'NEW' not in condition['states']:continue
        interface=condition.get('interface')
        if interface=='lo':continue
        if interface is not None:return None
        return action=='ACCEPT'
    return policy=='ACCEPT'

def validate_protocol(protocol: str) -> None:
    if protocol not in ('tcp', 'udp'):
        raise ValueError('Firewall protocol must be tcp or udp')


def validate_port(port: int) -> None:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Firewall port must be an integer in 1–65535')


def ufw_port_open(output: str, port: int, protocol: str) -> bool | None:
    """Recognize explicit unrestricted inbound rules, never a different protocol.

    Source/interface-specific rules, deny/limit ordering and unfamiliar output
    need operator review. An outbound ALLOW is not an inbound opening. When
    UFW reports IPv6 rules, require an unrestricted rule for both families.
    """
    if not re.search(r'^Status:\s+active\s*$', output, re.M | re.I):
        return None
    opened = set()
    ipv6 = False
    for line in output.splitlines():
        line = line.split('#', 1)[0].strip()
        if (not line or line.lower().startswith('status:')
                or re.fullmatch(r'To\s+Action\s+From', line, re.I)
                or re.fullmatch(r'[-\s]+', line)):
            continue
        match = re.fullmatch(r'(.*?)\s+(ALLOW|DENY|REJECT|LIMIT)(?:\s+(IN|OUT|FWD))?\s+(.*)', line, re.I)
        if not match:
            return None
        target, action, direction, source = match.groups()
        target, source = target.strip(), source.strip()
        if direction and direction.upper() in ('OUT', 'FWD'):
            continue
        ipv6 = ipv6 or '(v6)' in target
        exact = re.fullmatch(r'(\d+)/(tcp|udp)(\s+\(v6\))?', target, re.I)
        if exact:
            if int(exact[1]) != port or exact[2].lower() != protocol:
                continue
            family = 6 if exact[3] else 4
            anywhere = 'Anywhere (v6)' if family == 6 else 'Anywhere'
            if action.upper() != 'ALLOW' or source != anywhere:
                return None
            opened.add(family)
        elif (action.upper() != 'ALLOW'
              or re.fullmatch(re.escape(str(port)+'/'+protocol)+r'\s+.*', target, re.I)):
            # A range, application profile or broad deny might precede ALLOW.
            # An interface-scoped ALLOW is not approval to expose other NICs.
            return None
        # Ranges and application profiles are not proof that an
        # exact port is open. Explicit approval can add the precise rule.
    return 4 in opened and (not ipv6 or 6 in opened)


def port_open(info: dict, port: int, protocol: str = 'tcp') -> bool | None:
    validate_protocol(protocol)
    validate_port(port)
    backend=info["backend"]
    if backend=="ufw":
        value=run(["ufw","status"])
        if value.returncode:
            return None
        return ufw_port_open(value.stdout, port, protocol)
    if backend=="firewalld":
        if not info.get("managed") or not info.get("zone"):
            return None
        value=run(["firewall-cmd","--zone",info["zone"],"--query-port",str(port)+"/"+protocol])
        if value.returncode==0 and value.stdout.strip()=="yes": return True
        if value.returncode==1 and value.stdout.strip()=="no": return False
        return None
    if backend in ('iptables','iptables-nft') and 'input_rules' in info:
        result=input_port_open(info['input_rules'],port,protocol)
        # A known IPv4 block is actionable. An IPv4 allow never proves IPv6
        # exposure; only an explicitly disabled IPv6 stack removes that unknown.
        if result is True and not info.get('ipv6_disabled'):return None
        return result
    if backend=="none": return None
    return None

def open_ports(info: dict, ports: list[int], protocol: str = 'tcp') -> None:
    validate_protocol(protocol)
    for port in ports:
        validate_port(port)
    ports=sorted(set(ports))
    if info["backend"]=="ufw":
        for port in ports:
            result=subprocess.run(["ufw","allow",str(port)+"/"+protocol])
            if result.returncode: raise RuntimeError("UFW refused port "+str(port)+"/"+protocol)
        return
    if info["backend"]=="firewalld":
        if not info.get("managed") or not info.get("zone"):
            raise RuntimeError("Firewalld ingress zone requires manual configuration")
        # Confirmation may take time. Refuse to apply the approved ports to a
        # different (or now ambiguous) zone if bindings changed in the meantime.
        current=detect()
        if (current.get("backend")!="firewalld" or not current.get("managed")
                or current.get("zone")!=info["zone"]):
            raise RuntimeError("Firewalld ingress zone changed; rerun preflight or configure it manually")
        zone=info["zone"]
        for port in ports:
            for permanent in (False,True):
                command=["firewall-cmd","--zone",zone]
                if permanent:command.append("--permanent")
                command += ["--add-port",str(port)+"/"+protocol]
                if subprocess.run(command).returncode:
                    raise RuntimeError("firewalld refused port "+str(port)+"/"+protocol)
        return
    raise RuntimeError("Firewall backend is not safe for automatic modification")
