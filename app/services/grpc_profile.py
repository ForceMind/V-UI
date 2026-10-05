"""Literal service names verified with the pinned gRPC Lite server and clients."""
import re


def is_grpc_transport(value: object) -> bool:
    """Recognize imports for preservation guards, not public eligibility."""
    return (isinstance(value, dict) and isinstance(value.get("type"), str)
            and value["type"].lower() == "grpc")


def grpc_service_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        raise ValueError(
            "gRPC service_name must be 1–128 literal ASCII letters, digits, dots, underscores or hyphens"
        )
    return value
