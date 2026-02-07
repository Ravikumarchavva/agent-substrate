import json
import asyncio
from typing import List, Optional, Any, Dict

from agent_framework.agents.base_agent import BaseAgent
from agent_framework.tools import BaseTool
from agent_framework.model_clients.base_client import BaseModelClient
from agent_framework.memory.base_memory import BaseMemory
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.client_messages import (
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolExecutionResultMessage,
)
from agent_framework.messages.client_messages import ToolCallMessage
from opentelemetry.trace import Status, StatusCode
from agent_framework.observability import global_tracer, global_metrics, logger
from uuid import uuid4

class ReActAgent(BaseAgent):
    """
    Reasoning + Acting (ReAct) Agent that uses tools to solve problems.
    
    It operates in a loop:
    1. Observe current context (memory)
    2. Think (LLM generation)
    3. Act (Tool execution)
    4. Repeat until complete or max iterations reached.
    """

    def __init__(
        self,
        name: str,
        description: str,
        *,
        model_client: BaseModelClient,
        tools: List[BaseTool] | None = None,
        system_instructions: str = "You are a helpful AI assistant. Use the provided tools to solve the user's request. Think step-by-step.",
        memory: BaseMemory | None = None,
        max_iterations: int = 10,
        verbose: bool = True,
    ):
        super().__init__(
            name=name,
            description=description,
            model_client=model_client,
            tools=tools or [],
            system_instructions=system_instructions,
            memory=memory or UnboundedMemory(),
        )
        self.max_iterations = max_iterations
        self.verbose = verbose
        
        # Initialize system prompt if memory is empty
        if len(self.memory.get_messages()) == 0:
            self.memory.add_message(SystemMessage(content=self.system_instructions))

    async def run(self, input_text: str, **kwargs) -> Any:
        """
        Run the agent with the given user input.
        """
        attributes = {"agent_name": self.name, "input_length": len(input_text)}
        
        with global_tracer.start_span("agent_run", attributes) as span:
            global_metrics.increment_counter("agent_runs", tags={"name": self.name})
            
            logger.info(f"[{self.name}] Starting run for input: {input_text[:50]}...")
            
            # 1. Add User Message
            user_msg = UserMessage(content=input_text)
            self.memory.add_message(user_msg)
            
            # 2. ReAct Loop
            final_response = None
            
            for i in range(self.max_iterations):
                iteration_span_name = f"iteration_{i+1}"
                with global_tracer.start_span(iteration_span_name, {"iteration": i+1}) as iter_span:
                    if self.verbose:
                        logger.info(f"[{self.name}] Iteration {i+1}/{self.max_iterations}")

                    # A. Generate Response (THINK)
                    tool_schemas = [t.get_schema().to_openai_format() for t in self.tools]
                    messages = self.memory.get_messages()
                    
                    with global_tracer.start_span("llm_generate", {"msg_count": len(messages)}) as llm_span:
                        start_time = asyncio.get_event_loop().time()
                        try:
                            response = await self.model_client.generate(
                                messages=messages,
                                tools=tool_schemas if tool_schemas else None,
                                tool_choice="auto" if tool_schemas else None
                            )
                            end_time = asyncio.get_event_loop().time()
                            global_metrics.record_histogram("llm_latency", end_time - start_time, tags={"model": getattr(self.model_client, "model", "unknown")})
                        except Exception as e:
                            global_metrics.increment_counter("llm_errors", tags={"error": type(e).__name__})
                            raise e

                    # B. Update Memory
                    self.memory.add_message(response)
                    final_response = response
                    
                    # C. Check for Tool Calls (ACT)
                    if not response.tool_calls:
                        if self.verbose:
                            logger.info(f"[{self.name}] No tool calls, finishing.")
                        iter_span.set_attribute("outcome", "finished")
                        span.set_attribute("final_iterations", i + 1)
                        break
                    
                    if self.verbose:
                        logger.info(f"[{self.name}] Tool calls requested: {[ (tc.function['name'] if hasattr(tc, 'function') and isinstance(tc.function, dict) else (tc.name if hasattr(tc, 'name') else getattr(tc, 'id', None))) for tc in response.tool_calls ]}")

                    # D. Execute Tools
                    with global_tracer.start_span("execute_tools", {"count": len(response.tool_calls)}):
                        # Execute sequential for safety, but could be parallel
                        for tool_call in response.tool_calls:
                            # Support several tool_call shapes: new ToolCallMessage, legacy dicts, or Pydantic ToolCall
                            call_id = getattr(tool_call, "id", None)
                            tc_name = None
                            tc_args = None

                            if hasattr(tool_call, "function") and isinstance(tool_call.function, dict):
                                fn = tool_call.function
                                tc_name = fn.get("name")
                                raw_args = fn.get("arguments")
                                if isinstance(raw_args, str):
                                    try:
                                        tc_args = json.loads(raw_args)
                                    except Exception:
                                        tc_args = {}
                                else:
                                    tc_args = raw_args
                            elif isinstance(tool_call, ToolCallMessage):
                                tc_name = tool_call.name
                                tc_args = tool_call.arguments
                                call_id = tool_call.id
                            elif isinstance(tool_call, dict):
                                if "function" in tool_call and isinstance(tool_call["function"], dict):
                                    fn = tool_call["function"]
                                    tc_name = fn.get("name")
                                    raw_args = fn.get("arguments")
                                    if isinstance(raw_args, str):
                                        try:
                                            tc_args = json.loads(raw_args)
                                        except Exception:
                                            tc_args = {}
                                    else:
                                        tc_args = raw_args
                                else:
                                    tc_name = tool_call.get("name")
                                    tc_args = tool_call.get("arguments")
                            else:
                                # Pydantic ToolCall from tools.base_tool
                                if hasattr(tool_call, "name") and hasattr(tool_call, "arguments"):
                                    tc_name = getattr(tool_call, "name")
                                    tc_args = getattr(tool_call, "arguments")
                                    call_id = getattr(tool_call, "id", call_id)

                            call_id = call_id or str(uuid4())
                            tc_args = tc_args or {}

                            with global_tracer.start_span("tool_execution", {"tool": tc_name}) as tool_span:
                                tool = next((t for t in self.tools if t.name == tc_name), None)

                                if not tool:
                                    error_msg = f"Error: Tool '{tc_name}' not found."
                                    logger.error(f"[{self.name}] {error_msg}")
                                    tool_msg = ToolExecutionResultMessage(
                                        content=[{"type": "text", "text": json.dumps({"error": error_msg})}],
                                        tool_call_id=call_id,
                                        name=tc_name,
                                        isError=True
                                    )
                                    tool_span.set_status(Status(StatusCode.ERROR))
                                    global_metrics.increment_counter("tool_not_found_errors", tags={"tool": tc_name})
                                else:
                                    try:
                                        if self.verbose:
                                            logger.info(f"[{self.name}] Executing {tc_name} with {tc_args}")
                                        # EXECUTE - now returns ToolResult
                                        tool_result = await tool.execute(**tc_args)
                                        tool_msg = ToolExecutionResultMessage.from_tool_result(
                                            tool_result=tool_result,
                                            tool_call_id=call_id,
                                            tool_name=tc_name
                                        )
                                        global_metrics.increment_counter("tool_executions", tags={"tool": tc_name, "status": "success"})
                                    except Exception as e:
                                        error_msg = str(e)
                                        logger.error(f"[{self.name}] Tool execution failed: {e}")
                                        tool_msg = ToolExecutionResultMessage(
                                            content=[{"type": "text", "text": json.dumps({"error": error_msg})}],
                                            tool_call_id=call_id,
                                            name=tc_name,
                                            isError=True
                                        )
                                        tool_span.set_status(Status(StatusCode.ERROR))
                                        global_metrics.increment_counter("tool_execution_errors", tags={"tool": tc_name, "error": type(e).__name__})

                                # Add Result to Memory
                                self.memory.add_message(tool_msg)

            return final_response

    async def run_stream(self, input_text: str, **kwargs):
        """
        Run the agent with streaming, yielding partial responses and tool results as they are available.
        """
        attributes = {"agent_name": self.name, "input_length": len(input_text)}
        with global_tracer.start_span("agent_run_stream", attributes) as span:
            global_metrics.increment_counter("agent_runs", tags={"name": self.name})
            logger.info(f"[{self.name}] Starting streaming run for input: {input_text[:50]}...")

            # 1. Add User Message
            user_msg = UserMessage(content=input_text)
            self.memory.add_message(user_msg)

            # 2. ReAct Loop
            for i in range(self.max_iterations):
                iteration_span_name = f"iteration_{i+1}"
                with global_tracer.start_span(iteration_span_name, {"iteration": i+1}) as iter_span:
                    if self.verbose:
                        logger.info(f"[{self.name}] [stream] Iteration {i+1}/{self.max_iterations}")

                    tool_schemas = [t.get_schema().to_openai_format() for t in self.tools]
                    messages = self.memory.get_messages()

                    # A. Generate Response (THINK, streaming)
                    with global_tracer.start_span("llm_generate_stream", {"msg_count": len(messages)}) as llm_span:
                        start_time = asyncio.get_event_loop().time()
                        assistant_text = ""
                        final_response_obj = None
                        try:
                            async for response in self.model_client.generate_stream(
                                messages=messages,
                                tools=tool_schemas if tool_schemas else None,
                                tool_choice="auto" if tool_schemas else None,
                                **kwargs
                            ):
                                # If this response is a tool call, stop collecting text and handle tool execution
                                if response.tool_calls:
                                    # Construct a final response object using accumulated text and attach tool_calls
                                    final_response_obj = AssistantMessage(
                                        role="assistant",
                                        content=assistant_text,
                                        tool_calls=response.tool_calls,
                                        usage=response.usage,
                                    )
                                    # Yield the tool call event to external consumers and break to execute tools
                                    yield response
                                    break

                                # If it's a partial chunk (delta), append to the buffer and yield the chunk
                                partial_flag = response.metadata.get("partial") if getattr(response, "metadata", None) else None
                                if partial_flag is True and response.content:
                                    assistant_text += response.content
                                    yield response
                                else:
                                    # Could be final completion indicator - yield and continue
                                    if getattr(response, "metadata", None) and response.metadata.get("complete"):
                                        # Stream finished; mark final_response_obj if not set
                                        final_response_obj = final_response_obj or AssistantMessage(
                                            role="assistant",
                                            content=assistant_text,
                                            usage=response.usage,
                                            finish_reason=response.finish_reason,
                                        )
                                        yield response

                            end_time = asyncio.get_event_loop().time()
                            global_metrics.record_histogram("llm_latency", end_time - start_time, tags={"model": getattr(self.model_client, "model", "unknown")})
                        except Exception as e:
                            global_metrics.increment_counter("llm_errors", tags={"error": type(e).__name__})
                            raise e

                    # B. Update Memory with the assembled response
                    response = final_response_obj or AssistantMessage(
                        role="assistant",
                        content=assistant_text
                    )

                    # Heuristic: If the assistant produced a JSON object and no explicit tool_calls were present,
                    # try to map it to a single tool by matching parameter names. This covers models that return
                    # raw JSON arguments instead of a structured function_call event.
                    if not response.tool_calls and assistant_text:
                        try:
                            parsed = json.loads(assistant_text)
                            if isinstance(parsed, dict) and self.tools:
                                # Score tools by matching parameter names
                                candidates = []
                                for t in self.tools:
                                    schema = t.get_schema()
                                    params = {}
                                    fn = schema.get("function") or {}
                                    parameters = fn.get("parameters") or {}
                                    properties = parameters.get("properties") if isinstance(parameters, dict) else {}
                                    param_keys = set(properties.keys()) if properties else set()
                                    # Heuristic: candidate if parsed keys subset or non-empty intersection
                                    if param_keys and (set(parsed.keys()).issubset(param_keys) or set(parsed.keys()).intersection(param_keys)):
                                        candidates.append((t, param_keys))
                                if not candidates and len(self.tools) == 1:
                                    # If only one tool is available, assume it's intended
                                    chosen_tool = self.tools[0]
                                elif candidates:
                                    # Pick the candidate with the largest intersection
                                    candidates.sort(key=lambda x: len(set(parsed.keys()).intersection(x[1])), reverse=True)
                                    chosen_tool = candidates[0][0]
                                else:
                                    chosen_tool = None

                                if chosen_tool:
                                    tc = ToolCallMessage(id=response.id or str(uuid4()), name=chosen_tool.name, arguments=parsed)
                                    response.tool_calls = [tc]
                                    logger.info(f"[{self.name}] [stream] Heuristically detected tool call for '{chosen_tool.name}' with args: {parsed}")
                        except Exception:
                            # Not JSON or couldn't parse; ignore
                            pass

                    self.memory.add_message(response)

                    # C. Check for Tool Calls (ACT)
                    if not response.tool_calls:
                        if self.verbose:
                            logger.info(f"[{self.name}] [stream] No tool calls, finishing.")
                        iter_span.set_attribute("outcome", "finished")
                        span.set_attribute("final_iterations", i + 1)
                        break

                    if self.verbose:
                        logger.info(f"[{self.name}] [stream] Tool calls requested: {[ (tc.function['name'] if hasattr(tc, 'function') and isinstance(tc.function, dict) else (tc.name if hasattr(tc, 'name') else getattr(tc, 'id', None))) for tc in response.tool_calls ]}")

                    # D. Execute Tools (sequential, yield results)
                    with global_tracer.start_span("execute_tools_stream", {"count": len(response.tool_calls)}):
                        for tool_call in response.tool_calls:
                            # Support several tool_call shapes: new ToolCallMessage, legacy dicts, or Pydantic ToolCall
                            call_id = getattr(tool_call, "id", None)
                            tc_name = None
                            tc_args = None

                            if hasattr(tool_call, "function") and isinstance(tool_call.function, dict):
                                fn = tool_call.function
                                tc_name = fn.get("name")
                                raw_args = fn.get("arguments")
                                if isinstance(raw_args, str):
                                    try:
                                        tc_args = json.loads(raw_args)
                                    except Exception:
                                        tc_args = {}
                                else:
                                    tc_args = raw_args
                            elif isinstance(tool_call, ToolCallMessage):
                                tc_name = tool_call.name
                                tc_args = tool_call.arguments
                                call_id = tool_call.id
                            elif isinstance(tool_call, dict):
                                if "function" in tool_call and isinstance(tool_call["function"], dict):
                                    fn = tool_call["function"]
                                    tc_name = fn.get("name")
                                    raw_args = fn.get("arguments")
                                    if isinstance(raw_args, str):
                                        try:
                                            tc_args = json.loads(raw_args)
                                        except Exception:
                                            tc_args = {}
                                    else:
                                        tc_args = raw_args
                                else:
                                    tc_name = tool_call.get("name")
                                    tc_args = tool_call.get("arguments")
                            else:
                                # Pydantic ToolCall from tools.base_tool
                                if hasattr(tool_call, "name") and hasattr(tool_call, "arguments"):
                                    tc_name = getattr(tool_call, "name")
                                    tc_args = getattr(tool_call, "arguments")
                                    call_id = getattr(tool_call, "id", call_id)

                            call_id = call_id or str(uuid4())
                            tc_args = tc_args or {}

                            with global_tracer.start_span("tool_execution_stream", {"tool": tc_name}) as tool_span:
                                tool = next((t for t in self.tools if t.name == tc_name), None)

                                if not tool:
                                    error_msg = f"Error: Tool '{tc_name}' not found."
                                    logger.error(f"[{self.name}] [stream] {error_msg}")
                                    tool_msg = ToolExecutionResultMessage(
                                        content=[{"type": "text", "text": json.dumps({"error": error_msg})}],
                                        tool_call_id=call_id,
                                        name=tc_name,
                                        isError=True
                                    )
                                    tool_span.set_status(Status(StatusCode.ERROR))
                                    global_metrics.increment_counter("tool_not_found_errors", tags={"tool": tc_name})
                                else:
                                    try:
                                        if self.verbose:
                                            logger.info(f"[{self.name}] [stream] Executing {tc_name} with {tc_args}")
                                        # EXECUTE - now returns ToolResult
                                        tool_result = await tool.execute(**tc_args)
                                        tool_msg = ToolExecutionResultMessage.from_tool_result(
                                            tool_result=tool_result,
                                            tool_call_id=call_id,
                                            tool_name=tc_name
                                        )
                                        global_metrics.increment_counter("tool_executions", tags={"tool": tc_name, "status": "success"})
                                    except Exception as e:
                                        error_msg = str(e)
                                        logger.error(f"[{self.name}] [stream] Tool execution failed: {e}")
                                        tool_msg = ToolExecutionResultMessage(
                                            content=[{"type": "text", "text": json.dumps({"error": error_msg})}],
                                            tool_call_id=call_id,
                                            name=tc_name,
                                            isError=True
                                        )
                                        tool_span.set_status(Status(StatusCode.ERROR))
                                        global_metrics.increment_counter("tool_execution_errors", tags={"tool": tc_name, "error": type(e).__name__})
                                # Add Result to Memory and yield as ToolExecutionResultMessage
                                self.memory.add_message(tool_msg)
                                yield tool_msg

    def save_state(self):
        # Basic state dump
        return {
            "name": self.name,
            "memory_messages": [m.to_dict() for m in self.memory.get_messages()]
        }

    def load_state(self, state: Dict[str, Any]):
        if state.get("name") != self.name:
            logger.warning("Loading state for a different agent name")
        # Logic to restore memory would go here
        # For now, simplistic implementation
        pass
