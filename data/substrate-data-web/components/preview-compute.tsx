"use client";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "./ui/button";

// The application never starts compute: it can only route to compute an operator has attached.
export function PreviewCompute({ error }: { error: Error }) {
  const client = useQueryClient();
  return (
    <div className="compute-preview-paused" role="status">
      <p>Preview needs attached SQL compute that is ready.</p>
      <p className="muted">
        {error.message}. Start or attach a SQL worker where your compute runs,
        then retry. Schema and metadata are available without compute.
      </p>
      <Button
        variant="secondary"
        onClick={() => void client.invalidateQueries({ queryKey: ["preview"] })}
      >
        Retry preview
      </Button>
    </div>
  );
}
