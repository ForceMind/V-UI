"""Non-secret, read-only configuration eligibility for administration summaries.

The placeholder address deliberately proves no configured/public server address,
DNS, listener, certificate-file contents, network reachability, or relay policy.
Actual export requests still validate their supplied address and saved profile.
"""
from __future__ import annotations


_CONFIGURATION_HOST = "example.invalid"
_INVALID_PROFILE = (ValueError, TypeError, KeyError, AttributeError, UnicodeError,
                    OverflowError, RecursionError)


def export_eligibility(item) -> dict[str, bool]:
    """Reuse qualified exporters without disclosing artifacts or changing rows.

    Imports stay local so administration summaries do not create an import
    cycle through the subscription/export services. No draft exporter, profile
    compiler, credential generator, database operation, or runtime is invoked.
    """
    from app.services.validated_export import share_link
    from app.services.tcp_upstream_export import tcp_upstream_profile

    result = {"share_eligible": False, "upstream_export_eligible": False}
    # Legacy sharing accepts some truthy/coerced values. A corrupt stored row
    # must not be advertised as qualified by the new administration controls.
    if (item.enable is not True or type(item.expiry_time) is not int
            or item.expiry_time != 0 or type(item.core) is not str
            or type(item.protocol) is not str
            or type(item.settings) is not dict or type(item.stream_settings) is not dict
            or (item.remark is not None and type(item.remark) is not str)):
        return result
    try:
        # Qualify the actual ordinary link, including its URI serialization.
        # The generated credential-bearing value is discarded and never logged.
        share_link(item, _CONFIGURATION_HOST)
        result["share_eligible"] = True
    except _INVALID_PROFILE:
        pass
    try:
        tcp_upstream_profile(item, _CONFIGURATION_HOST)
        result["upstream_export_eligible"] = True
    except _INVALID_PROFILE:
        pass
    return result
