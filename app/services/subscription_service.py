"""Compatibility facade for subscription helpers.

New code lives in client_export.py and mihomo_subscription.py. This module is
kept so external scripts importing the early V-UI service path do not break.
"""

from app.services.client_export import (
    base64_subscription,
    export_warnings,
    mihomo_proxy,
    raw_links,
    share_link,
    singbox_client_config,
)
from app.services.mihomo_subscription import mihomo_config

__all__ = [
    "base64_subscription",
    "export_warnings",
    "mihomo_config",
    "mihomo_proxy",
    "raw_links",
    "share_link",
    "singbox_client_config",
]
