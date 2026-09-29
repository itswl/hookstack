---
title: The three boards share one layout, one set of parts and both languages
status: implemented
date: 2026-09-30
scope: stack
---

## Decision

The judge's board and the investigator's console were rebuilt on the layout the
pipe's board took when it became an inbox
([[the-board-reads-like-an-inbox]]). All three are now built from the same
parts, pinned like the palette.

- **One layout.** Every board has a sticky top bar with the name, one search,
  the language, the gear and the header controls. Under it is one tab strip.
  Every overview opens with one sentence saying whether anything needs a
  person, then the few numbers under it, then banners for what is broken. Lists
  are one row per item with a status in a word. The judge's tabs are board,
  verdicts, review and help. The console's are work, sessions, approvals, the
  knowledge group, the runtime group and help. On the pipe and the judge, an
  item opens in a drawer with its own address. The console opens it in the tab
  itself: a session keeps its list beside it, and a runbook or a role gets a
  page with a crumb back.
- **One set of parts.** `scripts/assert_design.py` pins ten blocks, three more
  than before. **Components** is the CSS of the top bar, buttons, fields, pill,
  headline, tiles, row, drawer, dialog and toast. **Dialogs** is the markup.
  **Kit** is the script: words in two languages, dates and durations, icons,
  the dialog, the toast, the clipboard, and a decoder for addresses. The design
  guard also checks each page's words: both languages must have the same keys
  and the same blanks, and every key a script writes out must exist. That means
  keys given as an argument or as either arm of a ternary. Keys a script builds
  from a prefix or a table are checked by the page's own tests
  (`test_board.py`, `test_board_words.py`, `test_console_words.py`).
- **Both languages, both modes, on all three.** The judge and the console now
  read in Chinese or English, like the pipe. The saved choice wins, then the
  browser's language. The pipe's older `hookrelay-lang` key is still read, so
  nobody's choice is lost. The live control is translated too. A dialog and a
  global shortcut never act on a key that belongs to an input-method
  composition.
- **Every function kept.** The console's `#session=`, `#skills/<name>` and
  `#audit/<key>` links still land, as do the judge's `#help` and `#review`.
  The console's timers went away. Its `window.confirm` calls and status
  labels became the shared dialog and toast.

Several fixes rode along. Most came from a second session's review of this
change before it was committed:
- The judge can now filter `/status` by return outcome (`ret`) in the query.
  The board counts dead returns over the whole window, and a filter over the
  newest page answered "no verdict matches" to the reader it had sent to look.
- `GET /judgements/{id}` reads one verdict however old, so a copied link
  outlives the newest page.
- Review rows carry `platform_importance`, so "the platform is right" sends a
  word the label accepts (`warning` means `medium`). The label door accepts the
  platform's own word too.
- The pipe's templates state a recovery flag only when the source said one:
  `state: ok` states nothing. Stating False for it outranked the judge's
  reading of a `[RESOLVED]` title, and the demo's recovery had been judged a
  repeat since 2026-09-28.
- The console names its node's real workdir. It marks a person's new session as
  the operator's, so an armed budget meter lets it through. It opens a patch
  with the token instead of a link that got a 401, and writes the live step
  feed into the running turn by id.

## Why

The operator asked for it: 「布局统一改一遍吧」, right after the pipe's board was
rebuilt as an inbox and all three boards gained dark and light. The palette had
been shared for weeks, but the pages still read like three products. There were
three kinds of button and two kinds of row. One page used `window.confirm` and
another a modal. The judge and the console spoke only English, while the
operator reads the pipe in Chinese on a phone.

The parts are pinned for the reason the palette was: a board that drifts into
its own button is found by a person squinting at two tabs, and nothing else
finds it. The words check is in the guard because a missing key renders as
`hero.waitng` on somebody's phone. Every test that only loads the page stays
green while it does.

The review mattered as much as the rebuild. Fifteen confirmed bugs were found
before the commit, most of them what a rebuild drops. HEAD had answered a
`ts`-less row with an empty string, and the kit's formatter threw. HEAD had
redrawn a session on every return, and the new redraw skip did not. HEAD had
clicked the way back, and the new links pointed at the address already shown.
Each got the fix and, where the shape allowed, a guard: the kit's time helpers
now print nothing for a missing time rather than throwing inside a list render.

## Consequences

- A new button, row or dialog is made once, in the pinned block, in all three
  files in one commit. That is the price of the palette, now paid for the parts
  too.
- A word added to one language and not the other, or asked for and not
  defined, turns the design gate red. A word built from a table is the page's
  own test's to name.
- The console lost its two-pane layout everywhere except sessions. The other
  views are full-width pages, which reads better on a phone and gives a long
  runbook the width it needs.
- The judge's documentation picture and six of the console's were reshot from
  one `demo.sh` run plus four more alerts, at no cost. The sessions, audit
  and waterfall pictures still show the older console, because they record real
  model runs from 2026-09-08. Reshooting them needs a model run (about $2.50).
  The prose beside the sessions picture had already drifted from that picture
  before this change.
