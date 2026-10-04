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

## Colour roles (one meaning each)

| Role | Colour | Used for |
|---|---|---|
| **Action** | orange (`accent-2`) | the main thing to do on a screen: Save, Connect, Send, Schedule. `Button` default `primary`. At most one or two per view. |
| **Selected / active** | violet (`accent`) | the current tab, active nav item, selected row, focus ring, checkmarks, progress, links. Never a button that does something. |
| **Neutral** | `secondary` / `ghost` | every other button. |
| **Danger** | red (`danger`) | delete and sign out. |

If a control changes the screen you are on, it is violet. If it makes something happen, it is orange.

## Adding things

- A new control: a `cva` component in `ui/`, heights from the control scale, behaviour from Radix, **and** a row in `/design-system`.
- A new colour: add a semantic token for both themes. Do not add a one-off hex.

## Layout and behaviour rules (apply everywhere)

- **Full-screen views are a `Page`** (`@/design`): one header (back, title, subtitle, actions) and a body that fills the width and height. Never a narrow centred column floating in empty space; "nothing here yet" is a `PageEmpty`, which fills the page too.
- **One way to scroll:** a region that scrolls takes the `scroll-area` class (smooth, contained, stable gutter, respects reduced motion). The header does not scroll.
- **Colour roles:** orange is the action (primary buttons), violet is selected/active; success/warning/danger are for state only. Tokens, never hex.
- **Dropdowns** are `Select`/`Menu`/`Combobox`; confirmations are `confirmAction`; feedback is `toast`. No native `select`, `confirm` or `alert`.
- **A settings page** is `Page` → `SettingGroup`s → `SettingRow`s: a titled bordered group, each row with its label and one-line description on the left and its control on the right (`Segmented` for 2-4 choices, `Select`/`Combobox`, `Button`, `Checkbox`). Long text opens in place (`Textarea`). No summary cards, no stacked forms. `Section` is the same panel when the content is a list or a form.
- **Use the width by meaning, never by splitting for its own sake.** Before laying out a wide page, sketch it: what belongs together, what is read together, what grows. Then pick the shape that fits that content:
  - *A list and what you opened from it* → list on the left, the item on the right (`Pane` + `Pane`; Scheduled). Narrow screens show one, with a back arrow.
  - *Settings made of unrelated groups* → `Page layout="columns"` (groups flow into two columns, General and Personalization), because each group stands alone.
  - *A dashboard* (limits, then totals, then a chart) → rows of figures that are read together, with the chart full width (Usage). Not two columns.
  - *One list or one table* → stays one block, full width (Storage, Approvals).
  If the content has no second part, leave the space empty rather than inventing one.
