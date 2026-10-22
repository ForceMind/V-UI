"""JSON-lines direct-function bridge, not an HTTP/auth/server test.

One interpreter and isolated real disk store live for one Node scenario. Import
production routes only after setting the temporary data directory. EOF, explicit
shutdown and SIGTERM all leave the TemporaryDirectory context and remove it.
"""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile


def emit(value):
    print(json.dumps(value), flush=True)


def terminate(_signum, _frame):
    raise SystemExit(0)


def main():
    signal.signal(signal.SIGTERM, terminate)
    with tempfile.TemporaryDirectory(prefix="vui-save-recovery-") as data_dir:
        os.environ["VUI_DATA_DIR"] = data_dir
        from fastapi import HTTPException, Response
        from pydantic import ValidationError
        from app.api import routing
        from app.services.routing_store import ROUTING_FILE

        assert ROUTING_FILE == Path(data_dir) / "mihomo-routing.json"
        emit({"ready": True, "data_dir": data_dir})
        for line in sys.stdin:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                emit({"id": request["id"], "closed": True})
                break
            if request.get("command") == "inspect":
                emit({"id": request["id"], "snapshot": routing.get_snapshot(),
                      "disk": ROUTING_FILE.read_text() if ROUTING_FILE.exists() else None})
                continue
            url, method = request["url"], request.get("method", "GET")
            response = Response()
            try:
                if (method, url) == ("GET", "/api/routing/mihomo/catalog"):
                    value = routing.get_catalog()
                elif (method, url) == ("GET", "/api/routing/mihomo/snapshot"):
                    value = routing.get_snapshot()
                elif (method, url) == ("GET", "/api/routing/mihomo/preview"):
                    value = routing.saved_preview()
                elif (method, url) == ("PUT", "/api/routing/mihomo"):
                    payload = routing.MihomoRoutingPayload.model_validate(request["payload"])
                    value = routing.update_settings(payload, response, request.get("revision"))
                else:
                    raise AssertionError((method, url))
                emit({"id": request["id"], "status": 200, "value": value,
                      "etag": response.headers.get("etag")})
            except HTTPException as exc:
                emit({"id": request["id"], "status": exc.status_code, "value": {"detail": exc.detail}})
            except ValidationError as exc:
                emit({"id": request["id"], "status": 422,
                      "value": {"detail": exc.errors(include_context=False)}})


if __name__ == "__main__":
    main()
