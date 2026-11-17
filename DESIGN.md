# viddown design system

viddown should feel like a precise tool for getting a video onto your own
device. It borrows from film stock and editing suites, not from SaaS dashboards.
The source of truth for values is [apps/mobile/src/theme/tokens.ts](apps/mobile/src/theme/tokens.ts).

## Colour

| Role | Light | Dark | Use |
|---|---|---|---|
| bg | `#EEF0F2` | `#1E2024` | App background ("aluminium" / "graphite") |
| surface | `#FFFFFF` | `#272A2F` | Rail, fields, raised panels |
| sunken | `#E3E6EA` | `#1A1C1F` | Hover / pressed, skeletons, segmented track |
| ink | `#16181D` | `#ECEDEF` | Text |
| inkMuted | `#555C69` | `#9EA4AE` | Secondary text (≥ 4.5:1 on bg and surface) |
| rule | `#D6DAE0` | `#383C43` | Hairlines |
| accent | `#F5C400` | `#F5C400` | Film-stock yellow, **only as a fill**, with ink on top |
| danger / success | `#B3311A` / `#1D7347` | `#FF8466` / `#62C990` | Status text |

Never set yellow text on a light background. Use exactly one accent. There are no gradients.

## Type

- **Archivo** for interface text and headings. Scale: 12, 14, 16, 18, 24, 36.
  The display style (36/40, weight 800, tight tracking) appears once per screen at most.
- **Archivo Narrow** with tabular figures for numbers that change, like
  timecodes, sizes, speeds, percentages and counts. It replaces the usual monospace
  "data label" look.
- Sentence case everywhere. No all-caps eyebrow labels, no middle-dot meta strings,
  no "→" in buttons.

## The frame bar (signature)

Progress is drawn as a strip of thin film frames that fill with yellow. Each
phase of a job is its own run of frames: Video, then Audio, then Merge (or Audio,
then Convert). The run widths come from `phaseSegments()` in `@viddown/core`, so they
always line up with the tracker's fractions. Paused and failed jobs show grey frames.
When the amount is unknown, a short cluster of frames travels along the bar; under reduced motion it stays still.

The same motif appears in the clip picker, where the kept range lights up, in the
selected-item marker (a single yellow frame) and in the wordmark.

Use it for progress and selection only. Never use it as decoration.

## Layout

- **Wide (≥ 840):** a labelled rail on the left. **Split (≥ 1180):** the Download
  screen shows the queue in a side panel.
- **Phones:** a bottom bar with Download, Queue, History and Settings. About is reached from Settings.
- Content is left-aligned with a 720 max width. Lists are divided rows, not cards.
- Radius follows hierarchy: field 12, control 8, chip 6, frame 1.

## Copy

- Buttons name the outcome: "Download 1080p60 clip", "Send message", "Paste a link".
- Errors say what happened and what to do. They don't apologise.
- Empty states point to the next action.

## Accessibility floor

- Visible focus ring on web/desktop (`Pressable` wrapper).
- Every icon-only button has a label, which also shows as a tooltip on desktop.
- Radio and switch roles on custom controls. Progress uses `progressbar` with a value.
- Clip handles are adjustable by screen readers, and the timecode fields give exact input.
- Reduced motion is respected.
