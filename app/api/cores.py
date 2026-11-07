import re
from fastapi import APIRouter, HTTPException, Request
from app.services.core_manager import CoreError, core_manager
from app.services.proxy_store import ProxyCapacityError, ProxyConflict

router = APIRouter()


def maintenance_result(request, value):
    """Keep useful runtime evidence without exposing internal exception text."""
    if getattr(getattr(request, 'state', None), 'maintenance', None) is None:
        return value
    if not isinstance(value, dict):
        return {}
    if any(key in value for key in ('xray', 'sing-box')):
        return {key: maintenance_result(request, value[key]) for key in ('xray','sing-box') if key in value}
    result = {}
    for key in ('binary_exists','running','enabled','config_exists','dirty','valid','applied'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key in ('pid',):
        if value.get(key) is None or type(value.get(key)) is int:
            result[key] = value.get(key)
    for key in ('applied_revision','desired_revision','candidate_revision'):
        item = value.get(key)
        if key in value:
            result[key] = item if isinstance(item,str) and re.fullmatch('[a-f0-9]{64}',item) else None
    if value.get('name') in ('xray','sing-box'):
        result['name'] = value['name']
    version = value.get('version_required')
    if isinstance(version,str) and re.fullmatch('[A-Za-z0-9._-]{1,64}',version):
        result['version_required'] = version
    for key in ('last_error','policy_error'):
        if key in value:
            result[key] = '核心或出口状态异常，请核对配置与维护记录' if value[key] else None
    return result

@router.get("/status")
def status(request: Request = None):
    return maintenance_result(request, core_manager.status())


def apply_checked(core: str, activate=True, *, preserve_stopped=True, expected_proxy_revision=None):
    try:
        if expected_proxy_revision is not None:
            return core_manager.apply_database(core, activate=activate, preserve_stopped=preserve_stopped,
                                               expected_proxy_revision=expected_proxy_revision)
        if preserve_stopped:
            return core_manager.apply_database(core, activate=activate)
        return core_manager.apply_database(core, activate=activate, preserve_stopped=False)
    except ProxyConflict:
        raise HTTPException(409, {'saved': True, 'applied': False, 'code': 'PROXY_REVISION_CONFLICT',
                                  'message': '出口设置已变化，节点已保存，请核对后再完成应用。'}) from None
    except ProxyCapacityError:
        raise HTTPException(409, {"saved": True, "applied": False,
            "code": "PROXY_CAPACITY_EXHAUSTED",
            "message": "Proxy history capacity is full; the existing runtime is unchanged"}) from None
    except CoreError as exc:
        raise HTTPException(409, {"saved": True, "applied": False, "message": str(exc)}) from exc


@router.post("/{core}/apply")
def validate(core: str, request: Request = None):
    return maintenance_result(request, apply_checked(core, activate=False))


@router.post("/{core}/restart")
def restart(core: str, request: Request = None):
    return maintenance_result(request, apply_checked(core, preserve_stopped=False))


@router.post("/{core}/stop")
def stop(core: str, request: Request = None):
    try:
        return maintenance_result(request, core_manager.stop(core))
    except CoreError as exc:
        raise HTTPException(409, str(exc)) from exc
