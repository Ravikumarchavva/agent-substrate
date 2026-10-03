"use client";

import { Check, ChevronDown } from "lucide-react";
import {
  Badge,
  Button,
  Menu,
  MenuContent,
  MenuItem,
  MenuSeparator,
  MenuSub,
  MenuSubContent,
  MenuSubTrigger,
  MenuTrigger,
  Text,
} from "@/design";
import { DEFAULT_REASONING, REASONING_LEVELS, reasoningLevelsFor, type ReasoningLevel } from "@/lib/model-preferences";
import type { ModelOption } from "@/types";

const label = (level: ReasoningLevel) => REASONING_LEVELS.find((l) => l.id === level)?.label ?? level;

/**
 * The composer's model chooser. The models, and, for a model that reasons, an "Effort" row that opens the levels that model accepts.
 * Models that are not available yet are listed but disabled.
 */
export function ModelPicker({
  models,
  selectedModel,
  onSelectModel,
  reasoning,
  onSelectReasoning,
}: {
  models: ModelOption[];
  selectedModel: string;
  onSelectModel: (id: string) => void;
  /** The effort in force for the selected model, or null when it does not reason. */
  reasoning: ReasoningLevel | null;
  onSelectReasoning: (level: ReasoningLevel) => void;
}) {
  const active = models.find((m) => m.id === selectedModel);
  const levels = reasoningLevelsFor(selectedModel);
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="ghost" aria-label="Choose model">
          <span className="truncate text-foreground">{active?.label ?? selectedModel.split("/").pop()}</span>
          {reasoning && <span className="text-muted">{label(reasoning)}</span>}
          <ChevronDown className="text-muted" />
        </Button>
      </MenuTrigger>
      <MenuContent side="top" align="end" className="w-60">
        {models.map((model) => (
          <MenuItem key={model.id} disabled={model.disabled} onSelect={() => onSelectModel(model.id)} className="items-start justify-between py-1.5">
            <span className="min-w-0">
              <span className="block truncate font-medium">{model.label}</span>
              <Text size="meta" tone="muted" className="truncate">{model.description}</Text>
            </span>
            {model.id === selectedModel && <Check className="mt-0.5 text-accent" />}
          </MenuItem>
        ))}
        <MenuSeparator />
        {!reasoning && (
          <MenuItem disabled className="justify-between">
            Effort
            <span className="text-xs text-muted">Not for this model</span>
          </MenuItem>
        )}
        {reasoning && (
          <>
            <MenuSub>
              <MenuSubTrigger value={label(reasoning)}>Effort</MenuSubTrigger>
              <MenuSubContent className="w-56">
                <Text size="meta" tone="muted" className="px-2.5 pb-1 pt-1.5">More effort is slower and uses your limit faster.</Text>
                {levels.map((level) => (
                  <MenuItem key={level} onSelect={() => onSelectReasoning(level)} className="justify-between">
                    <span className="flex items-center gap-2">
                      {label(level)}
                      {level === DEFAULT_REASONING && <Badge>Recommended</Badge>}
                    </span>
                    {level === reasoning && <Check className="text-accent" />}
                  </MenuItem>
                ))}
              </MenuSubContent>
            </MenuSub>
          </>
        )}
      </MenuContent>
    </Menu>
  );
}
