"""Every read route is guarded, and the list of the ones that are not is short.

The guard used to be a call each handler remembered to make: two header
parameters and one line, repeated seven times. A guard you can forget to call
is a guard that will eventually not be called — and the failure is silent, since
an unguarded route answers perfectly, just to anyone.

It is a route dependency now, which removes the forgetting from the handler and
moves it to the decorator. This test is what removes it from the decorator too.
"""

from __future__ import annotations

import inspect

from fastapi.routing import APIRoute

from hookrelay.app import create_app

# Deliberately reachable without a token, each for a stated reason. Adding to
# this list is the whole ceremony — it should cost a line and a thought.
PUBLIC = {
    "/": "the status page's markup; every call it then makes presents a token",
    "/card-action": "the confirm page a card's link lands on — the token is IN the link",
    "/healthz": "liveness for a container runtime, which has no credential to present",
    "/sw.js": "the page's service worker, part of the shell: it keeps the page and the icons and never the data",
}


def _get_routes(settings, cfg) -> list[APIRoute]:
    app = create_app(settings=settings, cfg=cfg)
    return [r for r in app.routes if isinstance(r, APIRoute) and "GET" in r.methods]


def test_every_get_route_is_guarded_or_named_public(settings, cfg) -> None:
    unguarded = []
    for route in _get_routes(settings, cfg):
        if route.path in PUBLIC:
            continue
        by_dependency = any(getattr(d.call, "__name__", "") == "_read_guard" for d in route.dependant.dependencies)
        by_admin_header = "x_admin_token" in inspect.signature(route.endpoint).parameters
        if not (by_dependency or by_admin_header):
            unguarded.append(route.path)
    assert not unguarded, (
        f"these GET routes present no credential requirement: {unguarded}. "
        "Add dependencies=[Depends(_read_guard)], take x_admin_token, or name it in PUBLIC with the reason."
    )


def test_the_public_list_still_describes_real_routes(settings, cfg) -> None:
    """A stale exemption is worse than none: it reads as a decision somebody
    made about a route that no longer exists, and hides the next one added
    under the same path."""
    paths = {route.path for route in _get_routes(settings, cfg)}
    assert set(PUBLIC) <= paths, f"PUBLIC names routes that are gone: {sorted(set(PUBLIC) - paths)}"


def test_the_guard_is_what_refuses(client) -> None:
    """The dependency is doing the work, not a leftover check inside a handler."""
    import anyio

    async def check() -> None:
        for path in ("/status", "/timeline", "/unseen", "/metrics"):
            assert (await client.get(path)).status_code == 401, path
            assert (await client.get(path, headers={"X-Read-Token": "read-t"})).status_code != 401, path

    anyio.run(check)
