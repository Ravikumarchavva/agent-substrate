/**
 * Public protocol surface for the UI.
 *
 * The interfaces are generated from the engine's Pydantic models
 * (see protocol.gen.ts — DO NOT edit that file). This module assembles them
 * into the `WireEvent` discriminated union and exposes the version guard.
 *
 * Import wire types from here, never from protocol.gen.ts directly.
 */
import {
  GENERATED_PROTOCOL_VERSION,
  HelloEvent,
  TextDeltaEvent,
  ReasoningDeltaEvent,
  ToolCallEvent,
  ToolResultEvent,
  HandoffEvent,
  TurnCompletedEvent,
  RunCompletedEvent,
  RunFailedEvent,
  RunCancelledEvent,
  ApprovalRequestedEvent,
  InputRequestedEvent,
  UIResourceEvent,
  ErrorEvent,
  PingEvent,
  ToolCallSummary,
  Attachment,
} from "./protocol.gen";

export type {
  HelloEvent,
  TextDeltaEvent,
  ReasoningDeltaEvent,
  ToolCallEvent,
  ToolResultEvent,
  HandoffEvent,
  TurnCompletedEvent,
  RunCompletedEvent,
  RunFailedEvent,
  RunCancelledEvent,
  ApprovalRequestedEvent,
  InputRequestedEvent,
  UIResourceEvent,
  ErrorEvent,
  PingEvent,
  ToolCallSummary,
  Attachment,
};

/** Every engine→UI SSE event. Discriminated on `type`. */
export type WireEvent =
  | HelloEvent
  | TextDeltaEvent
  | ReasoningDeltaEvent
  | ToolCallEvent
  | ToolResultEvent
  | HandoffEvent
  | TurnCompletedEvent
  | RunCompletedEvent
  | RunFailedEvent
  | RunCancelledEvent
  | ApprovalRequestedEvent
  | InputRequestedEvent
  | UIResourceEvent
  | ErrorEvent
  | PingEvent;

/** The wire event `type` discriminant values. */
export type WireEventType = NonNullable<WireEvent["type"]>;

/** Protocol version the engine generated these types from. */
export const PROTOCOL_VERSION = GENERATED_PROTOCOL_VERSION;

/**
 * Assert the engine's protocol version matches the one these types were
 * generated against. Called by the stream client on the `protocol.hello` event.
 * A mismatch means someone changed events.py without re-running codegen.
 */
export function assertProtocolVersion(engineVersion: string): void {
  if (engineVersion !== PROTOCOL_VERSION) {
    console.error(
      `[protocol] version mismatch: engine sent ${engineVersion}, ` +
        `UI generated against ${PROTOCOL_VERSION}. ` +
        `Run: cd ../ravi-engine && make protocol-schema && cd ../ravi-ui && pnpm gen:protocol`
    );
  }
}
