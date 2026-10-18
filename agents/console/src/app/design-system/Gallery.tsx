"use client";

import { ArrowLeft, ArrowRight, FileText, LayoutGrid, List, MessageSquare, MoreHorizontal, RefreshCw, Search, Settings2, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import {
  Badge,
  Button,
  Card,
  Container,
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogTrigger,
  Heading,
  Input,
  Menu,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  MenuTrigger,
  Meter,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Stack,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Text,
  Toolbar,
  ToolbarGroup,
  ToolbarItem,
  Tooltip,
  TooltipProvider,
} from "@/design";
import { useTheme } from "@/contexts/ThemeContext";

const SWATCHES = ["background", "surface", "surface-alt", "card", "card-hover", "border", "muted", "foreground", "accent", "accent-2", "success", "warning", "danger"];
const VARIANTS = ["primary", "secondary", "ghost", "danger"] as const;
const SIZES = ["sm", "md", "lg"] as const;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Stack gap="sm">
      <Heading level="label" className="uppercase tracking-wider text-muted">
        {title}
      </Heading>
      <Card className="p-4">{children}</Card>
    </Stack>
  );
}

export function DesignSystemGallery() {
  const { theme, toggleTheme } = useTheme();
  const [view, setView] = useState<"grid" | "list">("grid");
  const [size, setSize] = useState("M");

  return (
    <TooltipProvider>
      <div className="h-dvh overflow-y-auto bg-background py-8 text-foreground">
        <Container>
          <Stack gap="xl">
            <Stack direction="row" className="justify-between">
              <Stack gap="xs">
                <Heading level="page">Design system</Heading>
                <Text tone="muted">Tokens and primitives from <code>src/design</code>. Theme: {theme}.</Text>
              </Stack>
              <Button onClick={toggleTheme}>Toggle theme</Button>
            </Stack>

            <Section title="Colour tokens">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
                {SWATCHES.map((name) => (
                  <Stack key={name} gap="xs">
                    <div className="h-12 rounded-md border border-border" style={{ background: `var(--${name})` }} />
                    <Text size="meta" tone="muted">{name}</Text>
                  </Stack>
                ))}
              </div>
            </Section>

            <Section title="Type">
              <Stack gap="sm">
                <Heading level="page">Page heading</Heading>
                <Heading level="section">Section heading</Heading>
                <Heading level="title">Title</Heading>
                <Heading level="label">Label</Heading>
                <Text>Body text, 14px. The default for reading.</Text>
                <Text size="small" tone="muted">Small text, 12px, muted.</Text>
                <Text size="meta" tone="muted">Meta text, 11px, for dense metadata.</Text>
              </Stack>
            </Section>

            <Section title="Buttons: variant × size (heights 28 / 32 / 40)">
              <Stack gap="md">
                {VARIANTS.map((variant) => (
                  <Stack key={variant} direction="row" gap="sm">
                    <Text size="meta" tone="muted" className="w-20">{variant}</Text>
                    {SIZES.map((s) => (
                      <Button key={s} variant={variant} size={s}>
                        <RefreshCw /> {s}
                      </Button>
                    ))}
                    <Button variant={variant} size="icon-sm" aria-label="icon sm"><Settings2 /></Button>
                    <Button variant={variant} size="icon" aria-label="icon"><Settings2 /></Button>
                    <Button variant={variant} disabled>Disabled</Button>
                  </Stack>
                ))}
              </Stack>
            </Section>

            <Section title="Toolbar: one row, one height">
              <Toolbar data-testid="toolbar">
                <ToolbarGroup aria-label="History">
                  <ToolbarItem size="icon" aria-label="Back"><ArrowLeft /></ToolbarItem>
                  <ToolbarItem size="icon" aria-label="Forward"><ArrowRight /></ToolbarItem>
                </ToolbarGroup>
                <Button><RefreshCw /> Refresh</Button>
                <div className="relative w-48">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
                  <Input className="pl-8" placeholder="Search files…" />
                </div>
                <ToolbarGroup aria-label="View">
                  <ToolbarItem size="icon" active={view === "grid"} onClick={() => setView("grid")} aria-label="Grid"><LayoutGrid /></ToolbarItem>
                  <ToolbarItem size="icon" active={view === "list"} onClick={() => setView("list")} aria-label="List"><List /></ToolbarItem>
                </ToolbarGroup>
                <ToolbarGroup aria-label="Size">
                  {["S", "M", "L", "XL"].map((s) => (
                    <ToolbarItem key={s} size="icon" active={size === s} onClick={() => setSize(s)}>{s}</ToolbarItem>
                  ))}
                </ToolbarGroup>
              </Toolbar>
            </Section>

            <Section title="Inputs, badges, meters">
              <Stack direction="row" gap="md">
                <Input size="sm" placeholder="sm" className="w-32" />
                <Input placeholder="md" className="w-32" />
                <Input size="lg" placeholder="lg" className="w-32" />
                <Badge>neutral</Badge>
                <Badge tone="accent">accent</Badge>
                <Badge tone="success">success</Badge>
                <Badge tone="warning">warning</Badge>
                <Badge tone="danger">danger</Badge>
                <Meter icon={MessageSquare} label="Daily messages" used={0} limit={60} />
                <Meter icon={FileText} label="Daily documents" used={50} limit={60} />
                <Meter icon={FileText} label="Exhausted" used={60} limit={60} />
              </Stack>
            </Section>

            <Section title="Overlays">
              <Stack direction="row" gap="sm">
                <Menu>
                  <MenuTrigger asChild><Button><MoreHorizontal /> Menu</Button></MenuTrigger>
                  <MenuContent>
                    <MenuLabel><Text size="small">ravi@example.com</Text></MenuLabel>
                    <MenuSeparator />
                    <MenuItem><Settings2 /> Settings</MenuItem>
                    <MenuItem tone="danger"><Trash2 /> Delete</MenuItem>
                  </MenuContent>
                </Menu>
                <Popover>
                  <PopoverTrigger asChild><Button>Popover</Button></PopoverTrigger>
                  <PopoverContent><Text size="small">Anchored panel for small forms and pickers.</Text></PopoverContent>
                </Popover>
                <Tooltip label="Hover or focus hint"><Button variant="ghost" size="icon" aria-label="Info"><Settings2 /></Button></Tooltip>
                <Dialog>
                  <DialogTrigger asChild><Button>Dialog</Button></DialogTrigger>
                  <DialogContent title="Delete conversation?" description="This cannot be undone.">
                    <DialogFooter>
                      <DialogClose asChild><Button>Cancel</Button></DialogClose>
                      <DialogClose asChild><Button variant="primary">Delete</Button></DialogClose>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
              </Stack>
            </Section>

            <Section title="Tabs">
              <Tabs defaultValue="a">
                <TabsList>
                  <TabsTrigger value="a">General</TabsTrigger>
                  <TabsTrigger value="b">Storage</TabsTrigger>
                </TabsList>
                <TabsContent value="a" className="pt-3"><Text size="small" tone="muted">General settings.</Text></TabsContent>
                <TabsContent value="b" className="pt-3"><Text size="small" tone="muted">Storage settings.</Text></TabsContent>
              </Tabs>
            </Section>
          </Stack>
        </Container>
      </div>
    </TooltipProvider>
  );
}
