"""Makes `tests` a package so the suite can import its own helpers.

`from tests.helpers import FakeEngine, make_settings` needs this file; without
it the helpers would have to be a conftest fixture or a sys.path trick.
"""
