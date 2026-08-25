---
name: mobile-visual-qa
description: "Verify a mobile web UI on a real browser at device pixel ratio 2 (Android Chromium) instead of trusting unit tests, bounding boxes, or a vision model. Use for repeated screenshot bugs, 'looks off by a pixel', 'must look identical' contracts, touch/gesture behavior, and painted-ink verification of a rendered UI."
version: 1.0.0
---

# Mobile Visual QA (Real-Device, DPR2)

Static contracts and computed styles cannot prove a rendered UI looks right or
that a gesture works. Three sources give confident false verdicts:

- **Unit/contract tests** — pass while the pixels are wrong.
- **Bounding boxes / computed style** — a centred element can still *paint* its
  ink low; a high-specificity older selector can defeat an authoritative-looking
  tail rule.
- **Vision models** — comparing two dark rows, they invent tint differences that
  are not there.

So verify the painted pixels on a real browser at DPR 2.

## When to use

- A screenshot bug the user has reported more than once.
- "Off by a pixel", "not centered", "masih keliatan beda".
- A "these two rows/surfaces must look identical" contract.
- Any touch/gesture behavior (long-press select, drag-to-switch, pull/overscroll,
  popover dismiss).
- Before declaring a visual/interaction fix done.

## Setup

- Drive a headless (or real) Chromium via Selenium/Playwright at
  `deviceScaleFactor = 2` and a mobile viewport width.
- Serve the actual app; verify the **source the server returns**, not only files
  on disk (stale asset versions bite here).

## Painted-ink verification

- Screenshot at DPR 2 and measure ink extents per region rather than reading
  bounding boxes.
- For "must look identical except one icon" contracts, pixel-diff the two
  rendered rows/surfaces directly. Do not ask a vision model whether they match.
- To check centering, measure the glyph's painted ink, not the element box.

## Gesture verification

- Reproduce the real gesture (touch/pointer lifecycle), not a synthetic click,
  when the bug is about touch.
- For a popover/menu: open it, call `elementFromPoint()` at the center of a real
  action and assert it returns the action (not a scrim), then click once and
  assert the target surface opens. Test the full `pointerdown → click` sequence,
  because the same physical touch can leak to a newly exposed element.
- For a re-entry/stream race: seed history, start the stream, leave, return, and
  assert the messages and thinking indicator persist and deltas keep landing.
- Read `console` and assert zero errors after navigation and after each
  significant interaction — silent JS errors are high-value findings.

## Specificity trap

When a CSS edit "does nothing", grep the whole stylesheet for every rule
matching the element (`\.foo\b`, `\.parent>\.foo`, …), find the highest
specificity one, and match or exceed it. `!important` does not break a
specificity tie — specificity is compared first. Verify by computed style AND
painted pixels, never by assuming last-wins.

## Workflow

1. Identify the exact failing pixels/interaction from the latest screenshot; the
   user's correction overrides earlier prototypes.
2. Make the change; bump any changed static asset version strings.
3. Run syntax checks and the full frontend suite to green.
4. Launch Chromium at DPR 2, load the served app, reproduce the exact
   pixels/gesture, measure ink / run the pointer sequence, and read the console.
5. Only declare fixed after the real-browser check passes. Clean up probe
   artifacts.

## Pitfalls

- Do not celebrate a green unit suite as proof of a visual/touch fix.
- Do not trust a vision model on subtle tint/alignment.
- Do not verify against on-disk files when the server may serve a stale/older
  asset version.
