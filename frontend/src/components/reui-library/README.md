# ReUI component library (vendored)

The complete [ReUI](https://reui.io/components) component catalogue - Base UI
flavor, the same foundation as `src/components/ui/*` - vendored from
[keenthemes/reui](https://github.com/keenthemes/reui) (MIT, see LICENSE.md)
so dashboard templates can be picked and customized offline. The registry
endpoint at reui.io is license-gated; the source never was.

## Layout

| Folder       | What it holds |
| ------------ | ------------- |
| `ui/`        | The 60-odd core primitives (accordion … tooltip), Base UI flavor. |
| `reui/`      | ReUI's extended components: data-grid, kanban, stepper, timeline, tree, filters, rating, sortable, phone-input, … |
| `templates/` | The numbered example variants shown on reui.io/components (`c-<name>-<n>.tsx`) - the "pick a template, then customize" starting points. |
| `hooks/`, `lib/` | Shared helpers the above import (`cn`, use-mobile, use-file-upload, …). |

## Using a component

Nothing in here is imported by the app, so it adds zero bytes to the build
until you use it. Files are `.tsx`; Vite compiles TSX out of the box (esbuild
strips types, no type-checking), so they can be imported directly from `.jsx`:

```jsx
import { Timeline } from "@/components/reui-library/reui/timeline";
```

All internal imports were rewritten from the upstream registry paths to
`@/components/reui-library/...`, so components resolve each other in-place.

Two caveats:

1. **Peer dependencies.** Some components need packages this app does not
   ship: data-grid → `@tanstack/react-table`, kanban/sortable → `@dnd-kit/*`,
   chart → `recharts`, calendar/date-selector → `react-day-picker`/`date-fns`,
   carousel → `embla-carousel-react`. Install what the component you adopt
   imports.
2. **Styling tokens.** ReUI components use the same shadcn token names this
   app defines in `src/index.css` (`--primary`, `--muted`, …), so they render
   on-brand inside any `.cy-app` subtree. A few templates reference demo
   assets (`@/components/ui/svgs/*`) that were site chrome upstream - swap
   those for real content when adopting such a template.

## Updating

Re-vendor from the upstream repo and re-apply the path rewrite:
`@/registry/bases/base/` and `@/registry-reui/bases/base/` →
`@/components/reui-library/` (templates land in `templates/`), plus
`@/lib/utils` and `@/hooks/` → the library's own `lib/`/`hooks/`.
