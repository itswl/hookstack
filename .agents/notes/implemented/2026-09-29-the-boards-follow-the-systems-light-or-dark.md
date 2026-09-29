---
title: The three boards come in dark and light, and follow the system
status: implemented
date: 2026-09-29
scope: stack
---

## Decision

Every board — the pipe's, the judge's, the investigator's console — comes in a
dark and a light mode and follows the system's setting until the reader picks
the other one.

- **One set of roles, valued twice.** The pinned design tokens keep their names
  and gain a `:root[data-theme="light"]` set beside the dark one. Every text
  colour in the light set clears 4.5:1 on white and on its own 10% tint, the
  way a pill is drawn. The dark values are unchanged.
- **Chosen before the first paint.** A pinned snippet in each page's `<head>`
  sets `data-theme` from the reader's pick, or else from
  `prefers-color-scheme`, before anything is drawn.
- **One button, beside ↻, on all three.** It shows the mode in force, a sun
  or a moon. Pressing it picks the other mode; pressing back to what the system
  uses forgets the pick, so the same button is the way back to following the
  system. A system that changes its mind is followed live unless a pick
  stands. The pipe's settings drawer offers the three choices by name. The pick
  is kept in the browser under `hookstack-theme`, per board.
- **No page colour may ignore the mode.** A page's own colours are variables
  with a value in each mode. `scripts/assert_design.py` now pins seven blocks
  (the tokens, the header controls, the theme snippet and the theme wiring
  among them), requires the snippet to sit before `<body>`, and fails on any
  colour literal in a page's CSS outside the variable definitions; white, for
  text on a filled accent, is the one exception.

## Why

The operator asked for it: 「可以，适配深色与浅色模式」. They read the boards on a
phone, which follows the day and the room, and a page that stays dark on a
white phone is the one that looks broken.

Following the system is the default because it is what every other app on
that phone already does; the pick exists for the reader who wants otherwise,
and forgetting it on a press back to the system's mode avoids a third state
nobody could find. The snippet runs in `<head>` because anything later paints
the dark page first and then flips it — a flash on every load of a light phone.

Making the light set found what the dark-only pages had hidden: eighteen colour
literals on the investigator's console and fifteen on the pipe's own page —
buttons, selections, code, shadows, the skeleton sheen — each correct on dark
and wrong on white. Nothing would have caught the sixteenth, so the rule went
into the design guard rather than into a review note.

## Consequences

- A new colour on any page is a variable with two values, or the design gate is
  red. The literal it would have been is named in the failure.
- The three toggles are one pinned block, so a board cannot drift into its own
  behaviour; the price is the palette's price, a change is made in three files
  in one commit.
- Each board remembers its own pick: they are three origins, so the browser
  keeps three. A reader who picks light on one board and not the others sees
  them differ until they pick there too, or until the system and their pick
  agree.
- The documented screenshots were dark until 2026-09-30, when the operator
  asked for them light (「截图用浅色模式截图」). They are light now, English on
  the English pages and Chinese on the Chinese ones
  ([[the-three-boards-share-one-layout]]).
