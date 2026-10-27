"""Every URL path an app answers. Newer FastAPI keeps `include_router` routes inside a wrapper with no `.path` of its own, so walk into it."""


def route_paths(app) -> set:
    found: set = set()

    def walk(routes, prefix: str) -> None:
        for r in routes:
            path = getattr(r, "path", None)
            if path is not None:
                found.add(prefix + path)
            elif hasattr(r, "original_router"):
                walk(r.original_router.routes, prefix + (getattr(r.include_context, "prefix", "") or ""))

    walk(app.routes, "")
    return found
