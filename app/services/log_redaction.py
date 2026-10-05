"""Remove subscription bearer URLs from application access logging."""
import logging
import re

PATTERN = re.compile(r"/sub/[^\s\"']*", re.IGNORECASE)


class SubscriptionURLFilter(logging.Filter):
    def filter(self, record):
        def clean(value):
            return PATTERN.sub('/sub/[redacted]', value) if isinstance(value, str) else value
        record.msg = clean(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(clean(v) for v in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: clean(v) for k, v in record.args.items()}
        return True


def install_log_redaction():
    for name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, SubscriptionURLFilter) for f in logger.filters):
            logger.addFilter(SubscriptionURLFilter())
