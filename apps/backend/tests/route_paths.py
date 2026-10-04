"""Every route path on the app, including routes on included routers."""
from __future__ import annotations

from typing import Any, Iterable


def route_paths(app: Any) -> set[str]:
    def walk(routes: Iterable[Any], prefix: str) -> set[str]:
        out: set[str] = set()
        for route in routes:
            if hasattr(route, "path"):
                out.add(prefix + route.path)
            elif hasattr(route, "original_router"):
                context = getattr(route, "include_context", None)
                nested = context.get("prefix", "") if isinstance(context, dict) else getattr(context, "prefix", "")
                out |= walk(route.original_router.routes, prefix + str(nested or ""))
        return out

    return walk(app.routes, "")
