from __future__ import annotations

import os
import re
from pathlib import Path

from remote_observer_mcp.execution.model import ExecutionRequest, request_digest

_REQUEST_ID_RE = re.compile(r"^exec-[0-9]{8}-[0-9]{4}$")


def main() -> None:
    request_id = os.environ.get("REQUEST_ID", "")
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise SystemExit("invalid request ID")
    root = Path(os.environ.get("REMOTE_OBSERVER_REQUESTS", "execution_requests"))
    path = root / f"{request_id}.json"
    raw = path.read_bytes()
    request = ExecutionRequest.from_json_bytes(raw)
    if request.request_id != request_id:
        raise SystemExit("request ID mismatch")
    print(f"request_id={request.request_id}")
    print(f"risk={request.risk}")
    print(f"mode={request.mode}")
    print(f"digest={request_digest(raw)}")


if __name__ == "__main__":
    main()
