/* eslint-disable */
/**
 * GENERATED — DO NOT EDIT.
 * Source: agent-substrate/src/substrate/serving/protocol/ (Pydantic).
 * Regenerate: cd ../agent-substrate && make protocol-schema && cd ../substrate-ui && pnpm gen:protocol
 * Protocol version: 1.0.0
 */

export type Type = "protocol.hello";
export type Version = string;
export type Type1 = "text.delta";
export type Text = string;
export type Type2 = "reasoning.delta";
export type Text1 = string;
export type Type3 = "tool.call";
export type CallId = string;
export type ToolName = string;
export type Agent = string;
export type Depth = number;
export type Risk = string | null;
export type Type4 = "tool.result";
export type CallId1 = string;
export type ToolName1 = string;
export type Ok = boolean;
export type Output = string;
export type Error = string | null;
export type Agent1 = string;
export type Depth1 = number;
export type Type5 = "agent.handoff";
export type SourceAgent = string;
export type TargetAgent = string;
export type Reason = string;
export type Depth2 = number;
export type Type6 = "turn.completed";
export type Text2 = string;
export type Id = string;
export type Name = string;
export type Risk1 = string | null;
export type ToolCalls = ToolCallSummary[];
export type Id1 = string;
export type ThreadId = string | null;
export type Name1 = string;
export type Mime = string;
export type Size = number;
export type Url = string | null;
export type Attachments = Attachment[];
export type FinishReason = string;
export type Type7 = "run.completed";
export type Type8 = "run.failed";
export type Error1 = string;
export type Code = string | null;
export type Type9 = "run.cancelled";
export type Type10 = "approval.requested";
export type RequestId = string;
export type ToolName2 = string;
export type Type11 = "input.requested";
export type RequestId1 = string;
export type Prompt = string;
export type Options = string[] | null;
export type Type12 = "ui.resource";
export type CallId2 = string;
export type Uri = string;
export type MimeType = string;
export type Render = string;
export type Text3 = string;
export type Agent2 = string;
export type Depth3 = number;
export type Type13 = "error";
export type Message = string;
export type Code1 = string | null;
export type Type14 = "ping";

export interface SubstrateProtocol {
  WireEvent?:
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
}
/**
 * First event on every stream. Carries the protocol version for the client
 * to assert against its own generated types.
 */
export interface HelloEvent {
  type?: Type;
  version?: Version;
}
/**
 * Incremental assistant text, token by token.
 */
export interface TextDeltaEvent {
  type?: Type1;
  text: Text;
}
/**
 * Incremental reasoning / thinking trace.
 */
export interface ReasoningDeltaEvent {
  type?: Type2;
  text: Text1;
}
/**
 * An agent is about to execute a tool.
 */
export interface ToolCallEvent {
  type?: Type3;
  call_id?: CallId;
  tool_name: ToolName;
  args?: Args;
  agent?: Agent;
  depth?: Depth;
  risk?: Risk;
}
export interface Args {
  [k: string]: unknown;
}
/**
 * A tool finished.
 */
export interface ToolResultEvent {
  type?: Type4;
  call_id?: CallId1;
  tool_name: ToolName1;
  ok?: Ok;
  output?: Output;
  error?: Error;
  agent?: Agent1;
  depth?: Depth1;
  structured_content?: Record<string, unknown>;
}
/**
 * An orchestrator delegated to a subagent.
 */
export interface HandoffEvent {
  type?: Type5;
  source_agent?: SourceAgent;
  target_agent?: TargetAgent;
  reason?: Reason;
  depth?: Depth2;
}
/**
 * One assistant turn finished. If ``tool_calls`` is non-empty the agent will
 * continue after the tools run (another turn follows); otherwise this is the
 * final assistant message of the run.
 */
export interface TurnCompletedEvent {
  type?: Type6;
  text?: Text2;
  tool_calls?: ToolCalls;
  attachments?: Attachments;
  finish_reason?: FinishReason;
}
/**
 * A tool call attached to a completed assistant turn (for history rebuild).
 */
export interface ToolCallSummary {
  id?: Id;
  name: Name;
  args?: Args1;
  risk?: Risk1;
}
export interface Args1 {
  [k: string]: unknown;
}
/**
 * A file produced during the turn (image, document, …).
 */
export interface Attachment {
  id: Id1;
  thread_id?: ThreadId;
  name: Name1;
  mime?: Mime;
  size?: Size;
  url?: Url;
}
/**
 * The whole agent run finished successfully.
 */
export interface RunCompletedEvent {
  type?: Type7;
  reason?: string;
}
/**
 * The run terminated with an unrecoverable error.
 */
export interface RunFailedEvent {
  type?: Type8;
  error?: Error1;
  code?: Code;
}
/**
 * The run was cancelled by the client.
 */
export interface RunCancelledEvent {
  type?: Type9;
}
/**
 * The agent is waiting for the human to approve a tool call.
 */
export interface ApprovalRequestedEvent {
  type?: Type10;
  request_id: RequestId;
  tool_name: ToolName2;
  args?: Args2;
}
export interface Args2 {
  [k: string]: unknown;
}
/**
 * The agent is waiting for free-form human input.
 */
export interface InputRequestedEvent {
  type?: Type11;
  request_id: RequestId1;
  question: string;
  context?: string;
  options?: Array<{ key: string; label: string; description?: string }>;
  allow_freeform?: boolean;
}
/**
 * A tool produced an interactive UI to render in a sandboxed iframe.
 *
 * The single carrier for every rich UI (kanban, chart, form, map, …): a
 * ``ui://`` resource reference plus the data to feed it, per MCP Apps. The
 * host renders ``uri`` and pushes ``structured_content`` over the postMessage
 * channel. A later event with the same ``call_id`` + ``uri`` updates an
 * already-mounted iframe rather than remounting it.
 */
export interface UIResourceEvent {
  type?: Type12;
  call_id?: CallId2;
  uri: Uri;
  mime_type?: MimeType;
  structured_content?: StructuredContent;
  render?: Render;
  text?: Text3;
  agent?: Agent2;
  depth?: Depth3;
}
export interface StructuredContent {
  [k: string]: unknown;
}
/**
 * A serving-level error (distinct from ``run.failed`` which is agent-level).
 */
export interface ErrorEvent {
  type?: Type13;
  message: Message;
  code?: Code1;
}
/**
 * Keep-alive heartbeat.
 */
export interface PingEvent {
  type?: Type14;
}

export const GENERATED_PROTOCOL_VERSION = "1.0.0";
