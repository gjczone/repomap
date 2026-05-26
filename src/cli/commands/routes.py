from __future__ import annotations

import sys

from ... import json_dumps
from ...ai import render_routes_report
from ..handlers import (
    CLI_NAME,
    _scan_engine,
    _scan_stats_payload,
    _route_payload,
)


def run_routes(
    project: str, max_files: int, as_json: bool, with_consumers: bool = False
) -> int:
    try:
        engine = _scan_engine(project, max_files)
        if as_json:
            payload = {
                "command": "routes",
                "project": str(engine.project_root),
                "scanStats": _scan_stats_payload(engine),
                "routes": [_route_payload(route) for route in engine.list_routes()],
            }
            if with_consumers:
                from ...consumers import find_route_consumers

                consumers = find_route_consumers(engine, engine.list_routes())
                consumer_json = {}
                for key, clist in consumers.items():
                    consumer_json[key] = [
                        {
                            "file": c.file,
                            "line": c.line,
                            "context": c.context,
                            "confidence": c.confidence,
                            "match_type": c.match_type,
                        }
                        for c in clist
                    ]
                payload["consumers"] = consumer_json
            print(json_dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if with_consumers:
            from ...consumers import find_route_consumers

            consumers = find_route_consumers(engine, engine.list_routes())
            print(render_routes_report(engine, consumers))
        else:
            print(render_routes_report(engine))
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] routes failed: {exc}", file=sys.stderr)
        return 1
