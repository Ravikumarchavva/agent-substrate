"use client";

import { Check, ChevronDown } from "lucide-react";
import { Button, Menu, MenuContent, MenuItem, MenuTrigger, Text } from "@/design";
import type { ModelOption } from "@/types";

/** The composer's model chooser. Opens upward from the composer; models that are not available yet are listed but disabled. */
export function ModelPicker({
  models,
  selectedModel,
  onSelectModel,
}: {
  models: ModelOption[];
  selectedModel: string;
  onSelectModel: (id: string) => void;
}) {
  const active = models.find((m) => m.id === selectedModel);
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="ghost" aria-label="Choose model">
          <span className="truncate text-foreground">{active?.label ?? selectedModel.split("/").pop()}</span>
          <ChevronDown className="text-muted" />
        </Button>
      </MenuTrigger>
      <MenuContent side="top" align="end" className="w-64">
        {models.map((model) => (
          <MenuItem
            key={model.id}
            disabled={model.disabled}
            onSelect={() => onSelectModel(model.id)}
            className="items-start justify-between py-1.5"
          >
            <span className="min-w-0">
              <span className="block truncate font-medium">{model.label}</span>
              {model.description && <Text size="meta" tone="muted" className="line-clamp-2">{model.description}</Text>}
            </span>
            {model.id === selectedModel && <Check className="mt-0.5 text-accent" />}
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  );
}
