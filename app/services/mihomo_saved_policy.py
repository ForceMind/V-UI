"""Pure protected compilation of one privately stored YAML plan snapshot."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, asdict
import hashlib
import ipaddress
import re

from app.services.inbound_policy import PolicyError
from app.services.mihomo_merge_plan import StaticMihomoPlan
from app.services.mihomo_plan_policy import protected_yaml_route
from app.services.mihomo_plan_record import PlanRecordError, StoredMihomoPlan, decode_plan_record
from app.services.proxy_policy import (
    AddressPolicy, CompiledPolicy, DNS_TAG, LOCAL_TAG, NAMESPACE, NORMAL_DNS_TAG, UPSTREAM_TAG,
    VerifiedEndpoint, _digest, _prepare, _upstream,
)
from app.services.upstream_profile import UpstreamProfile


YAML_NAMESPACE = 'vui-yaml-'


@dataclass(frozen=True)
class CompiledYamlPolicy(CompiledPolicy):
    yaml_plan_revision: str = ''
    endpoints: tuple[tuple[str, VerifiedEndpoint], ...] = field(default=(), repr=False)
    dns_upstream: str | None = field(default=None, repr=False)


def yaml_endpoint_id(record_id, name):
    raw = b'vui-yaml-endpoint-v1\0' + record_id.encode('ascii') + b'\0' + name.encode('utf-8')
    return 'up_' + hashlib.sha256(raw).hexdigest()[:32]


def compile_saved_yaml_policy(base_config, inbounds, snapshot, *, endpoints, self_addresses, policy=None):
    """Consume a validated Store snapshot and independently checked endpoint map."""
    if (type(base_config) is not dict or type(snapshot) is not dict
            or type(snapshot.get('revision')) is not str or not re.fullmatch('[a-f0-9]{64}', snapshot['revision'])
            or snapshot.get('execution_mode') != 'yaml'
            or type(snapshot.get('yaml_plan')) is not tuple or len(snapshot['yaml_plan']) != 2
            or type(snapshot['yaml_plan'][0]) is not StoredMihomoPlan):
        raise PolicyError('INVALID_YAML_SNAPSHOT')
    record = snapshot['yaml_plan'][0]
    try:
        checked = decode_plan_record(record._raw)
    except PlanRecordError:
        raise PolicyError('INVALID_YAML_SNAPSHOT') from None
    if record.record_id != checked.record_id:
        raise PolicyError('INVALID_YAML_SNAPSHOT')
    manifest = checked.value
    sources = snapshot['yaml_plan'][1]
    if (type(sources) is not dict or set(sources) != {'baseline', 'candidate', 'merged'}
            or any(type(raw) is not bytes or not 0 < len(raw) <= 1_048_576
                   or hashlib.sha256(raw).hexdigest() != manifest['sources'][role]
                   for role, raw in sources.items())):
        raise PolicyError('INVALID_YAML_SNAPSHOT')
    profiles = tuple(UpstreamProfile.from_dict(value) for value in manifest['upstreams'])
    yaml_plan = StaticMihomoPlan(profiles, tuple(map(tuple, manifest['rules'])),
                                 tuple(map(tuple, manifest['source_rules'])), tuple(manifest['group_members'].items()))
    config = deepcopy(base_config)
    rows = list(inbounds)
    settings, source, coverage, _, selected_tags = _prepare(
        config, rows, snapshot, require_upstream=False, namespaces=(NAMESPACE, YAML_NAMESPACE))
    metadata = dict(plan_manifest=source.manifest, policy_version=3, source_fingerprint=source.source_fingerprint)
    if type(endpoints) is not dict:
        raise PolicyError('YAML_ENDPOINT_CONTEXT_MISMATCH')
    if not source.requires_upstream:
        if endpoints:
            raise PolicyError('YAML_ENDPOINT_CONTEXT_MISMATCH')
        identity = _digest({'policy_version': 3, 'config': config, 'yaml_plan': checked.record_id,
                            'source_fingerprint': source.source_fingerprint, 'endpoints': {}})
        return CompiledYamlPolicy(config, coverage, identity, **metadata, yaml_plan_revision=checked.record_id,
                                  dns_upstream=manifest['dns_upstream'])
    policy = AddressPolicy() if policy is None else policy
    if type(policy) is not AddressPolicy or not self_addresses:
        raise PolicyError('MISSING_ADDRESS_SNAPSHOT')
    names = {profile.name: profile for profile in profiles}
    referenced = {target for _, _, target in yaml_plan.rules if target not in {'DIRECT', 'REJECT'}}
    dns_name = manifest['dns_upstream']
    needs_dns = any(kind in ('DOMAIN', 'DOMAIN-SUFFIX', 'MATCH') and target != 'REJECT'
                    for kind, _, target in yaml_plan.rules)
    if needs_dns and dns_name is None:
        raise PolicyError('YAML_DNS_UPSTREAM_REQUIRED')
    if dns_name is not None:
        referenced.add(dns_name)
    if endpoints.keys() != referenced:
        raise PolicyError('YAML_ENDPOINT_CONTEXT_MISMATCH')
    pins = {}
    endpoint_ids = set()
    for name in sorted(referenced):
        endpoint = endpoints[name]
        profile = names[name]
        expected_id = yaml_endpoint_id(checked.record_id, name)
        if (type(endpoint) is not VerifiedEndpoint or endpoint.profile_id != expected_id
                or endpoint.revision != snapshot['revision'] or endpoint.host != profile.server
                or type(endpoint.addresses) is not tuple or not 1 <= len(endpoint.addresses) <= 32
                or type(endpoint.resolved_at) is not int or endpoint.resolved_at < 0
                or expected_id in endpoint_ids):
            raise PolicyError('YAML_ENDPOINT_CONTEXT_MISMATCH')
        endpoint_ids.add(expected_id)
        verified = policy.check_addresses(endpoint.addresses, self_addresses=self_addresses)
        if verified != endpoint.addresses:
            raise PolicyError('YAML_ENDPOINT_CONTEXT_MISMATCH')
        try:
            literal = ipaddress.ip_address(profile.server)
        except ValueError:
            literal = None
        if literal is not None and verified != (str(literal),):
            raise PolicyError('ENDPOINT_LITERAL_MISMATCH')
        pins[name] = verified
    tags = {profile.name: (UPSTREAM_TAG if profile.name == dns_name else YAML_NAMESPACE + 'upstream-' + str(index))
            for index, profile in enumerate(profiles)}
    tags['DIRECT'] = YAML_NAMESPACE + 'direct'
    if settings['local_proxy_enabled']:
        config.setdefault('inbounds', []).append({
            'type': 'mixed', 'tag': LOCAL_TAG, 'listen': '127.0.0.1',
            'listen_port': settings['local_port'], 'set_system_proxy': False,
            'users': [{'username': settings['local_username'], 'password': snapshot['local_password']}],
        })
        selected_tags.append(LOCAL_TAG)
    outbounds = config.setdefault('outbounds', [])
    outbounds.append({'type': 'direct', 'tag': tags['DIRECT']})
    for name in sorted(referenced):
        outbound = _upstream(names[name], pins[name][0])
        outbound['tag'] = tags[name]
        outbounds.append(outbound)
    route = config.setdefault('route', {})
    if type(route) is not dict or type(route.get('rules', [])) is not list or type(route.get('rule_set', [])) is not list:
        raise PolicyError('INVALID_BASE_ROUTE')
    if dns_name is not None:
        dns = config.setdefault('dns', {})
        servers = dns.setdefault('servers', [])
        ordinary = not settings['relay_enabled'] if source.legacy else any(
            item['enable'] and item['effective'] == 'direct' for item in source.manifest)
        if not servers and ordinary:
            servers.append({'type': 'local', 'tag': NORMAL_DNS_TAG})
            dns.setdefault('final', NORMAL_DNS_TAG)
        servers.append(policy.dns_server())
        if len(servers) > 1:
            route.setdefault('default_domain_resolver', dns.get('final') or servers[0]['tag'])
    protected = protected_yaml_route(yaml_plan, inbounds=tuple(selected_tags), outbound_tags=tags,
                                     dns_tag=DNS_TAG, self_addresses=self_addresses, upstream_addresses=pins,
                                     policy=policy, dns_upstream=dns_name)
    route['rules'] = protected['rules'] + route.get('rules', [])
    route['rule_set'] = protected['rule_set'] + route.get('rule_set', [])
    wire_endpoints = {name: asdict(endpoints[name]) for name in sorted(endpoints)}
    identity = _digest({'policy_version': 3, 'config': config, 'yaml_plan': checked.record_id,
                        'source_fingerprint': source.source_fingerprint, 'endpoints': wire_endpoints})
    return CompiledYamlPolicy(config, coverage, identity, **metadata, yaml_plan_revision=checked.record_id,
                              endpoints=tuple(sorted(endpoints.items())), dns_upstream=dns_name)
