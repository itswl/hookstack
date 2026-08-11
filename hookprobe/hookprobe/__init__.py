"""hookprobe — a single-purpose deep-analysis agent runner.

Fourth member of the hook* family: hookrelay is the pipe, hookjudge is the
judge, WebhookWise is the comprehensive orchestrator — hookprobe is the
investigator. It accepts one analysis task over an OpenClaw-compatible HTTP
contract, runs one unattended read-only agent session, and serves the final
report to whoever polls for it. Nothing else.
"""

__version__ = "0.1.0"
