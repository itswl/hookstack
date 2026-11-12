---
title: On a phone the three boards are laid out as apps, with a tab bar at the bottom
status: implemented
date: 2026-11-12
scope: stack
---

## Decision

On a phone (640px and narrower) the three boards take an app's frame; on a
desk the layout is what it was, refined.

- **The tab strip is a tab bar** pinned to the bottom edge, frosted, one row of
  equal cells with the icon over the name. The page on screen is marked by a
  soft pill behind its icon; a count that needs somebody (`.count.warn`,
  `.count.bad`) is a filled badge on the icon, any other count a quiet one. The
  console's two groups stay one cell each, and a group's own pages are a
  segmented control under the large title.
- **The bar steps aside while a field is typed in**, and comes back on the blur.
- **A large title** names the page on screen; once it has scrolled under the
  header, the header shows the name in small, centred, and draws its bottom line.
- **The header keeps what a thumb needs:** the name, a search button that opens
  the search field under it, the gear, and the live dot, with the word shown
  only when the board is not live. Language, light or dark and ↻ are left to the
  settings drawer, where all three boards already offered them.
- **A dialog is a sheet** rising from the bottom edge, its answers full width
  and 46px tall; **a toast is a capsule** dropping from under the status bar.
- **An installed board refreshes when pulled** down from the top. It presses the
  same ↻ a desk does, and it is wired only where nothing else answers a pull: an
  installed board, on a touch screen, with no drawer or dialog open.
- **Depth, glass and motion are tokens** valued per mode: `--elev`/`--elev-up`
  for cards, `--glass`/`--hairline` for the bars, `--ease`/`--spring` for what
  slides, and `--num` (the system's rounded face, figures of one width) for
  numbers a person counts. The overview's one sentence glows from its card's
  corner in the colour of what it says, and its badge sends out a slow ring when
  something needs somebody. A shown page fades and rises 8px into place. Under
  `prefers-reduced-motion` all of it stands still.
- **The home-screen icons** are drawn on a surface lit from above with a faint
  glow of the accent behind the glyph, and iOS is handed the full-bleed icon.

The new script is one pinned block, `hookstack app shell`, the twelfth in
`scripts/assert_design.py`.

## Why

The operator called the boards crude as apps and asked for taste ("这个 ui
太简陋了 … 做的有 taste 一点，给人惊喜"). Rendered at 414px before the change,
the header held seven controls in two rows (the name, the language, the gear,
light or dark, ↻, the live pill, and the search on its own row), and the tabs
sat under it, out of a thumb's reach, in a strip that read as a web page's.

**The tab bar was turned down once**, on 2026-10-06
(`2026-10-06-three-boards-install-on-a-phone.md`), for two reasons, and both
were checked before this reversed it:

- *A fixed bar fights the composer and the keyboard.* It did, the way any bar
  does: the keyboard lifts a fixed bottom bar over the field it opened for. So
  the bar now leaves while a text field, a textarea or a select has focus, the
  way the phone's own apps hide theirs, and the console's session panes end above
  the bar (`--tabbar-h`), so the composer is never under it. Measured headless:
  the bar is `display: none` with the session composer focused and back on blur.
- *The operator's other installed board took its own out.* It has one again: its
  design language (read 2026-11-12) lays a phone out as a fixed header and a
  bottom tab bar for the places an on-call person opens most.

That other board also paid for a lesson this keeps: the View Transitions API,
used there for a change of page, flashed its fixed tab bar and swallowed the taps
of a whole transition, by design. A change of page here is a plain CSS animation
on the view being shown, which captures nothing and blocks nothing, and a live
refetch, which redraws inside a view without showing it again, never replays it.

Everything else follows from the bar: once the strip is at the bottom, the name
of the page is no longer on screen, which is what the large title is for; the
second row of a group's pages had nowhere to go but under that title; and with
the tabs gone from the top, the header could shed what a phone reaches through
the gear anyway. A browser tab keeps its own pull to refresh, so the board's
pull is wired only in an installed board, which has none.

## Consequences

- Measured headless on the stack smoke's data, every page of the three boards
  (126 renders: 375px and 414px, both languages, light, and dark at 375px): no
  page wider than the screen, no tab name cut, one row in the bar on all three
  boards, every field at 16px; the pull arms at 64px of travel
  and presses ↻ once; the header's small title follows the scroll both ways; the
  group's segmented control presses the strip's own tabs, so the page's router
  is still the only one, and it follows a language switch.
- On a desk the layout and every control are where they were; the cards gained a
  shadow on white and a top edge in the dark, and numbers moved from `--mono` to
  `--num`.
- The same sweep found the console's Approvals squeezing a report's title into
  a column one word wide beside its buttons, on a phone; its two tables are one
  block per row there now, like the flight recorder. And the desk's margin under
  the strip had lifted the tab bar 20px off the bottom edge; the phone zeroes it.
- A home-screen icon on a phone keeps the image it was added with. The new icons
  reach a phone only when the icon is added again, like the status-bar style.
- The tab shell's phone rules and the app shell together decide what a phone
  shows; a fourth board gets both by copying the blocks.
- Not done: swipe-back from the edge to close a drawer, and a count-up of the
  overview's numbers. Each is a gesture or a timer the boards do not have yet.
