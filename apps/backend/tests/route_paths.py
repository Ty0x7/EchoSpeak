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


def iter_routes(app: Any) -> list[tuple[str, Any]]:
    """(full path, route) for every route, including routes on included routers."""
    def walk(routes: Iterable[Any], prefix: str) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for route in routes:
            if hasattr(route, "path"):
                out.append((prefix + route.path, route))
            elif hasattr(route, "original_router"):
                context = getattr(route, "include_context", None)
                nested = context.get("prefix", "") if isinstance(context, dict) else getattr(context, "prefix", "")
                out.extend(walk(route.original_router.routes, prefix + str(nested or "")))
        return out

    return walk(app.routes, "")


def patch_api(monkeypatch: Any, name: str, value: Any) -> None:
    """Patch a shared API helper in every api module that imported it by name."""
    import sys

    import api.server  # noqa: F401  (loads every route module)

    hits = 0
    for module_name, module in list(sys.modules.items()):
        if (module_name == "api" or module_name.startswith("api.")) and hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            hits += 1
    assert hits, f"no api module defines {name}"
