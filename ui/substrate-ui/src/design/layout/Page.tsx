import type { LucideIcon } from "lucide-react";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../cn";
import { Button } from "../ui/Button";

/**
 * The heading of a full-screen view: the page's name in large type, what it is for beneath it, the page's own actions on the right and, when
 * the page sits inside another (a task inside Scheduled), a back arrow. No bar, no border: it is part of the page, as in ChatGPT and Claude.
 */
export function PageHeading({ title, subtitle, onBack, backClassName, actions }: { title: string; subtitle?: string; onBack?: () => void; backClassName?: string; actions?: ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      {onBack && (
        <Button variant="ghost" size="icon" aria-label="Back" onClick={onBack} className={cn("-ml-2 mt-0.5", backClassName)}>
          <ArrowLeft />
        </Button>
      )}
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2 pt-1">{actions}</div>}
    </div>
  );
}

/** One scrolling pane with the page's padding and no heading of its own: half of a split page (a list beside what you opened). */
export function Pane({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="scroll-area h-full min-h-0 w-full">
      <div className={cn("flex min-h-full flex-col gap-6 px-4 pb-8 pt-16 sm:px-8 sm:pt-8", className)}>{children}</div>
    </div>
  );
}

/**
 * Every full-screen view in the product (Scheduled, Approvals, Notifications, and every settings page) is a `Page`:
 *
 *   <Page title="Memory" subtitle="…" actions={<Button/>}>
 *     <SettingGroup title="…">…</SettingGroup>
 *   </Page>
 *
 * A `PageHeading` at the top of one scrolling area (no separate header bar). The page always fills the whole area, edge to edge. Nothing else sets its own heading size or scroll.
 *
 * Width is for using, not stretching. `layout="columns"` (settings pages made of separate groups) flows the groups into two columns once the page itself is
 * wide enough (a container query, so a collapsed sidebar widens it too), and a group that needs the whole width is marked `data-span="all"`. A page that is one
 * list or one table keeps `layout="single"` and splits the space its own way (a list beside the item you opened, a grid of cards).
 */
export function Page({ title, subtitle, onBack, actions, children, className, layout = "single" }: { title: string; subtitle?: string; icon?: LucideIcon; onBack?: () => void; actions?: ReactNode; children: ReactNode; className?: string; layout?: "single" | "columns" }) {
  return (
    <div className="scroll-area @container h-full min-h-0 w-full bg-background text-foreground">
      <div className={cn("flex min-h-full flex-col gap-6 px-4 pb-8 pt-16 sm:px-8 sm:pt-8", className)}>
        <PageHeading title={title} subtitle={subtitle} onBack={onBack} actions={actions} />
        {layout === "columns" ? (
          <div className="space-y-6 @4xl:block @4xl:columns-2 @4xl:gap-6 @4xl:space-y-0 @4xl:[&>*]:mb-6 @4xl:[&>*]:break-inside-avoid @4xl:[&>[data-span=all]]:[column-span:all]">{children}</div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

/**
 * A titled group on a settings page: a bordered panel of `SettingRow`s, the way every settings screen people know is laid out.
 *
 *   <SettingGroup title="Appearance">
 *     <SettingRow label="Theme" description="Match your system or pick one."><Segmented …/></SettingRow>
 *   </SettingGroup>
 *
 * `Section` is the same group when its content is not rows (a list, a form).
 */
export function SettingGroup({ title, description, children, className }: { title: string; description?: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn("space-y-2", className)}>
      <div className="px-1">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {description && <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p>}
      </div>
      <div className="divide-y divide-border rounded-xl border border-border bg-background/40">{children}</div>
    </section>
  );
}

/** One setting: what it is on the left, its control on the right (under the text on a narrow screen). */
export function SettingRow({ label, description, children, className }: { label: string; description?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3", className)}>
      <div className="min-w-0 flex-1 basis-56">
        <p className="text-sm font-medium text-foreground">{label}</p>
        {description && <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p>}
      </div>
      {children && <div className="ml-auto flex shrink-0 items-center gap-2">{children}</div>}
    </div>
  );
}

/** A group whose content is not rows (a list, a form). Same panel, same heading. */
export function Section({ title, description, children, className }: { title: string; description?: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn("space-y-2", className)}>
      <div className="px-1">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {description && <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p>}
      </div>
      <div className="rounded-xl border border-border bg-background/40 p-4">{children}</div>
    </section>
  );
}

/** "Nothing here yet": an icon tile, what this page is for, and what to do first. Fills the page like a Storage folder does. */
export function PageEmpty({ icon: Icon, title, children, action }: { icon: LucideIcon; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-border bg-background/40 px-6 py-16 text-center">
      <div className="flex size-12 items-center justify-center rounded-xl bg-badge text-muted">
        <Icon className="size-6" aria-hidden />
      </div>
      <p className="mt-4 text-sm font-semibold text-foreground">{title}</p>
      {children && <div className="mt-1 max-w-md text-xs leading-relaxed text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
