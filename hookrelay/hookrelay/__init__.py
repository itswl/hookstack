"""hookrelay — receive webhooks, decide, fan out to channels.

Importing the package registers the built-in source adapters, processors and
channel types; plugins add theirs at startup via registry.load_plugins().
"""

__version__ = "0.2.0"

from hookrelay import adapters as _adapters  # noqa: F401  (registration)
from hookrelay import channels as _channels  # noqa: F401
from hookrelay import processors as _processors  # noqa: F401
