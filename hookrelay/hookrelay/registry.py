"""The three extension points, as one small registry.

hookrelay is a skeleton with three sockets:

    source adapter — HOW an upstream knocks (verification scheme, payload shape)
    processor      — WHAT happens to an event between door and routing
    channel        — WHERE a routed message goes and what it looks like on the wire

Built-ins register themselves through the same decorators a plugin uses, so
"built-in" is a statement about packaging, not privilege. Plugins are plain
.py files in a directory (HOOKRELAY_PLUGINS, default ./plugins), imported at
startup BEFORE the config is validated — config references adapters by name
and unknown names must fail the boot, not the first event.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import Any

SOURCE_ADAPTERS: dict[str, Any] = {}
PROCESSORS: dict[str, Any] = {}
CHANNEL_BUILDERS: dict[str, Callable[..., Any]] = {}
# What a person can do from a message on this channel type: `callbacks` (a
# pressed button reaches /card-action) or `links` (a URL they open). Declared by
# the builder, asked by the app — so nothing outside this registry needs an IM
# platform's NAME to know whether an alert sent there can be acted on. A plugin
# that declares nothing is assumed to offer nothing, which is the safe default.
CHANNEL_CAPABILITIES: dict[str, frozenset[str]] = {}


def source_adapter(name: str) -> Callable[[Any], Any]:
    def register(obj: Any) -> Any:
        if name in SOURCE_ADAPTERS:
            raise ValueError(f"source adapter {name!r} registered twice")
        SOURCE_ADAPTERS[name] = obj() if isinstance(obj, type) else obj
        return obj

    return register


def processor(name: str) -> Callable[[Any], Any]:
    def register(obj: Any) -> Any:
        if name in PROCESSORS:
            raise ValueError(f"processor {name!r} registered twice")
        PROCESSORS[name] = obj() if isinstance(obj, type) else obj
        return obj

    return register


def channel(name: str, *, capabilities: tuple[str, ...] = ()) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(builder: Callable[..., Any]) -> Callable[..., Any]:
        if name in CHANNEL_BUILDERS:
            raise ValueError(f"channel type {name!r} registered twice")
        CHANNEL_BUILDERS[name] = builder
        CHANNEL_CAPABILITIES[name] = frozenset(capabilities)
        return builder

    return register


def channel_can(name: str, capability: str) -> bool:
    return capability in CHANNEL_CAPABILITIES.get(name, frozenset())


def load_plugins(directory: str | Path) -> list[str]:
    """Import every top-level .py in the directory, sorted for determinism.

    A plugin is ordinary Python that imports hookrelay.registry and decorates.
    Import errors are fatal on purpose: a half-loaded plugin set silently
    changes routing behaviour, which is worse than refusing to start.
    """
    loaded: list[str] = []
    root = Path(directory)
    if not root.is_dir():
        return loaded
    for path in sorted(root.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"hookrelay_plugin_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load plugin {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded.append(path.stem)
    return loaded
