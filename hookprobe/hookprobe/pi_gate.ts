/**
 * The tool gate, as a pi extension.
 *
 * This file is not a second posture. It is a translator: pi calls it before a
 * tool runs, it hands the call to `python -m hookprobe.gate` — the same module
 * the Claude adapter calls in-process and the Codex adapter spawns per tool —
 * and it turns the one answer into pi's shape.
 *
 * Everything policy-shaped is on the Python side on purpose. What lives here is
 * the vocabulary difference: pi names its tools in lower case, the gate speaks
 * the names Claude Code and Codex both use, and neither of them should have to
 * learn the other's.
 *
 * Configuration arrives in the environment the adapter spawned pi with, which
 * child processes inherit. Nothing is read from the tool call itself except the
 * call being judged: a gate the agent can argue with is not a gate.
 */

import { spawnSync } from "node:child_process";

/** pi's tool names, in the vocabulary hookprobe.gate already speaks. */
const TOOL_NAMES: Record<string, string> = {
  bash: "Bash",
  write: "Write",
  edit: "Edit",
  multiedit: "MultiEdit",
  read: "Read",
};

function toolName(name: string): string {
  // An MCP tool keeps its name: the allowlist is written in `mcp__server__tool`
  // form and matching it is the whole point of the guard.
  return TOOL_NAMES[name] ?? name;
}

function ask(payload: Record<string, unknown>): Record<string, any> {
  const python = process.env.HOOKPROBE_GATE_PYTHON || "python3";
  const done = spawnSync(python, ["-m", "hookprobe.gate"], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    timeout: 20000,
  });
  if (done.error || done.status !== 0) {
    // Fails CLOSED, and deliberately without consulting the payload: if the
    // gate cannot be reached, the honest answer to "may this run" is no. A gate
    // whose failure mode is permissive is the one nobody finds out about.
    throw new Error(`the tool gate did not answer: ${done.error?.message ?? done.stderr ?? "no output"}`);
  }
  try {
    return JSON.parse(done.stdout || "{}");
  } catch {
    throw new Error(`the tool gate answered with something that was not JSON: ${done.stdout?.slice(0, 200)}`);
  }
}

export default function (pi: any) {
  pi.on("tool_call", (event: any) => {
    const answer = ask({
      hook_event_name: "PreToolUse",
      tool_name: toolName(String(event.toolName ?? "")),
      tool_input: event.input ?? {},
      tool_use_id: event.toolCallId,
    });
    const decision = answer.hookSpecificOutput ?? {};
    if (decision.permissionDecision === "deny") {
      // `terminate` is deliberately absent. A refusal is information the agent
      // should have to work around — it can pick a read-only path instead — and
      // ending the turn on the first denial would throw away an investigation
      // over one badly chosen command.
      return { block: true, reason: String(decision.permissionDecisionReason ?? "refused by this node's posture") };
    }
    return undefined;
  });

  pi.on("tool_result", (event: any) => {
    // The flight recorder. Never allowed to affect the run: an audit that can
    // break a turn will be the first thing somebody turns off.
    try {
      ask({
        hook_event_name: "PostToolUse",
        tool_name: toolName(String(event.toolName ?? "")),
        tool_input: event.input ?? {},
        tool_response: { is_error: Boolean(event.isError) },
        tool_use_id: event.toolCallId,
      });
    } catch {
      /* recorded nothing; the turn continues */
    }
    return undefined;
  });
}
