"""The bridge reads its chat and secret from the environment at import; give it a test set first."""

import os
import sys
from pathlib import Path

os.environ.setdefault("LARK_CHAT_ID", "oc_default")
os.environ.setdefault("BRIDGE_CHAT_IDS", "oc_example")
os.environ.setdefault("BRIDGE_INBOUND_SECRET", "s3")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
