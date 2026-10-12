# What you get

The feature list long enough to live off the front page. Each line names the
thing it is, and the page that shows it working.

- **Only the work worth doing.** A storm, a restatement or a recovery never buys
  a second verdict. On 795 production alerts, 28 of 29 rules answered the same
  every time, so `rule-reuse` answers them for free
  ([the measurement](../hookjudge/eval/README.md), [the judge](../hookjudge/README.md)).
- **A card a person can rule on.** Every button is signed before the card leaves,
  and every press is recorded ([the token](../hookrelay/hookrelay/actions.py)).
- **Remediation with a person in the loop.** An approved procedure runs step by
  step against an allowlist, as argv, never through a shell. A press approves the
  version that was read: the card and the console both name the procedure's
  digest, and one that changed since is refused. A plan a person hands off is
  carried out by a separate work node, with a write credential of its own and a
  guard that refuses destructive commands; it made its first real change on
  2026-09-30 ([how it went](deployments.md#the-first-real-write)).
- **Investigations that leave something behind.** A finished run distills a
  runbook, and the next occurrence starts from it
  ([learning](../hookprobe/docs/learning.md)).
- **Agents you can contain.** Read-only by default and measured at startup,
  budget ceilings that refuse out loud, and the structural boundaries, each
  written up with what it does **not** stop ([containment](containment.md)).
- **Boards that install on your phone.** All three boards are web apps you add to
  the home screen, the investigator's under its node's own name; each keeps its
  tokens in that browser and nothing else, and offline it shows the page and says
  its service is out of reach. On a phone the header runs under the status bar in
  its own colour and follows the theme, the tabs fit one row, and wide tables
  become one block per row.
- **One audit page per event.** Every hop, digest, decision and human action, a
  flight recorder of every tool call, and a timing waterfall per run
  ([the pipe's audit](../hookrelay/README.md), [the investigator's](../hookprobe/README.md)).
- **Your model, your chat.** Any Anthropic-dialect endpoint for the investigator,
  any OpenAI-compatible one for the judge, local models included. Feishu/Lark
  through a bridge ([protocol](bridge-protocol.md)), DingTalk and WeCom as a
  plugin, or signed JSON to any webhook.

The three boards read in Chinese or English and follow the system's light or
dark. The screenshots in the [overview](../OVERVIEW.md) come from local runs, not
mockups.
