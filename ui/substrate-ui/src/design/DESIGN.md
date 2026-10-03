# Design system

One set of tokens and one set of primitives for the chat UI and the platform. **This folder is the source of truth and lives in
`substrate-ui`.** `agent-substrate-platform/src/design/` is an identical copy made by `scripts/sync-design.sh`; CI in both repos fails
if they differ. Edit here, then sync. Never edit the copy.

## Rules

1. **Use a primitive.** Buttons, inputs, menus, dialogs come from `design/ui`. No raw `<button>` or `<input>` outside this folder.
2. **Use a token.** Colours are Tailwind names that read a token (`bg-card`, `text-muted`, `border-border`). No hex in `.tsx`, no
   `bg-(--card)` in new code.
3. **Use the scale.** Sizes come from the scales below. No `[13px]`, `h-[72px]`. If a value is missing, add it to the scale here
   with a reason; do not inline it.
4. **Controls share a height.** Every interactive control is `sm` (28), `md` (32) or `lg` (40) via the primitive, so a row of
   controls aligns by construction. Touch targets grow to 44 only on coarse pointers (`@layer base`), never globally.
5. **Layout is structure.** Spacing between siblings comes from `Stack` / `gap-*`, not margins on each child.

## Scales (tokens.css)

| Scale | Values |
|---|---|
| Spacing | Tailwind's 4px grid: 1, 2, 3, 4, 6, 8, 12, 16, 24 |
| Type | `text-2xs` 11 · `xs` 12 · `sm` 14 · `base` 16 · `lg` 18 · `xl` 20 · `2xl` 24 · `3xl` 30 |
| Radius | `sm` 6 · `md` 8 (controls) · `lg` 12 (panels) · `xl` 16 (floating surfaces) · `full` (pills, avatars) |
| Control height | `sm` 28 · `md` 32 (default) · `lg` 40 |
| Motion | `--duration-fast/normal/slow`, `--ease-*` |

## Colour layers

1. **Scales**: raw sizes; the same in every theme.
2. **Semantic**: `background`, `foreground`, `muted`, `border`, `surface`, `card`, `card-hover`, `success/warning/danger`. Light and
   dark swap here only.
3. **Brand**: `accent`, `accent-hover`, `accent-foreground`, `accent-2`. An instance re-brands by overriding these in its own CSS
   after importing `tokens.css`. Components never name a brand colour.

## Adding things

- A new control: a `cva` component in `ui/`, heights from the control scale, behaviour from Radix, **and** a row in `/design-system`.
- A new colour: add a semantic token for both themes. Do not add a one-off hex.
