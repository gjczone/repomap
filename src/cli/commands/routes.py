from __future__ import annotations

import sys

from ...reports import render_routes_report
from ...hints import routes_hint
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
            from ..handlers import json_envelope

            payload = {
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
            print(json_envelope("routes", str(engine.project_root), payload))
            return 0
        if with_consumers:
            from ...consumers import find_route_consumers

            consumers = find_route_consumers(engine, engine.list_routes())
            print(render_routes_report(engine, consumers))
        else:
            print(render_routes_report(engine))
        routes = engine.list_routes()
        for hint in routes_hint(has_routes=len(routes) > 0):
            print(hint, file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[{CLI_NAME}] routes failed: {exc}", file=sys.stderr)
        return 1
