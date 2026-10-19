from __future__ import annotations

from collections.abc import AsyncIterator

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.conversations.chat.model import (
    ApprovalOut,
    CancelResponse,
    ChatRequest,
    HitlResponse,
    HitlStatus,
)
from loamz.agents.features.conversations.threads.service import ThreadsService, threads
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.runtime import ChatPayload, Message, Runtime
from loamz.agents.kernel.tools.approval import ApprovalDecision
from loamz.agents.kernel.types.content import ChatMessage, Role, TextBlock
from loamz.agents.kernel.types.identity import Actor
from loamz.agents.kernel.types.run_log import RunLogKind
from loamz.agents.kernel.wire import wire_from_log
from loamz.agents.kernel.wire.streaming import TERMINAL
from loamz.agents.stores import AgentStore
from loamz.data.storage.errors import ConflictError, InvalidRequestError, NotFoundError


class ChatService:
    def __init__(self, container: Container, threads_service: ThreadsService) -> None:
        self._c = container
        self._threads = threads_service

    @property
    def _rt(self) -> Runtime:
        return self._c.runtime

    @property
    def _store(self) -> AgentStore:
        return self._c.agent_store

    def tool_catalog(self) -> list[object]:
        return []

    async def chat(self, caller: Caller, req: ChatRequest) -> AsyncIterator[str]:
        header = await self._threads.require(caller, req.thread_id)
        if header.locked_at is not None:
            raise InvalidRequestError(header.locked_reason or "This conversation is locked.")

        active = await self._rt.find_runs(thread_id=req.thread_id, active_only=True)
        if active:
            raise ConflictError(f"A stream is already running for thread {req.thread_id}.")

        raw_prompt = req.prompt_text()
        if not raw_prompt:
            raise InvalidRequestError("Message prompt cannot be empty.")

        prompt = raw_prompt
        if req.file_ids:
            from loamz.agents.features.workspace.files.service import FilesService

            files_svc = FilesService(self._c)
            for fid in req.file_ids:
                try:
                    data, filename = await files_svc.download(caller, fid)
                    content = data.decode("utf-8", errors="replace")
                    prompt += f"\n\n[Attached file: {filename}]\n{content}"
                except Exception:
                    pass

        agent_id = req.agent_id or header.agent_id or "default"
        sender_actor = Actor(type="user", key=caller.user_id)

        from loamz.agents.kernel.agents.factory import create_assistant_agent

        agent = create_assistant_agent(
            model_client=self._c.model_client(req.model),
            history=self._c.agent_store.threads,
            tools=None,
            system_instructions=req.system_instructions or "",
            session_id=agent_id,
            name="agent",
        )
        await self._rt.register(agent, pinned=False)

        message = Message(
            target=agent.id,
            sender=sender_actor,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text=prompt)])
            ),
            correlation_id=req.thread_id,
            metadata={"display_text": raw_prompt},
        )
        run_id = await self._rt.submit(
            agent.id,
            message,
            tenant=caller.tenant_id,
            max_retries=0,
            thread_id=req.thread_id,
        )

        async for entry in self._rt.tail(run_id):
            wire = wire_from_log(entry.kind, entry.payload or {})
            if wire is not None:
                yield f"event: {wire.type}\ndata: {wire.model_dump_json()}\n\n"
            if entry.kind == RunLogKind.RUN_COMPLETED:
                yield 'data: {"type": "run.completed"}\n\n'
                break
            elif entry.kind == RunLogKind.RUN_FAILED:
                err = (entry.payload or {}).get("error", "agent run failed")
                yield f'data: {{"type": "run.failed", "error": "{err}"}}\n\n'
                break
            elif entry.kind == RunLogKind.RUN_CANCELLED:
                yield 'data: {"type": "run.cancelled"}\n\n'
                break
            elif entry.kind in TERMINAL:
                break
        yield "data: [DONE]\n\n"

    async def respond_hitl(
        self, caller: Caller, request_id: str, body: HitlResponse
    ) -> dict[str, object]:
        runs = await self._rt.find_runs(active_only=True)
        target_run_id: str | None = None
        for run in runs:
            if run.tenant == caller.tenant_id:
                pending = await self._rt.pending_approvals(run.run_id)
                for p in pending:
                    if p.request_id == request_id:
                        target_run_id = run.run_id
                        break
            if target_run_id:
                break

        if not target_run_id:
            raise NotFoundError(f"Approval request {request_id} not found or already decided.")

        action_map = {
            "approve": ApprovalDecision.APPROVED,
            "approved": ApprovalDecision.APPROVED,
            "reject": ApprovalDecision.DENIED,
            "deny": ApprovalDecision.DENIED,
            "denied": ApprovalDecision.DENIED,
            "modify": ApprovalDecision.MODIFIED,
            "modified": ApprovalDecision.MODIFIED,
        }
        decision = action_map.get(body.action.lower())
        if decision is None:
            raise InvalidRequestError(f"Invalid approval decision {body.action}")

        # Invariant I23: Server names the approver
        await self._rt.decide_approval(
            run_id=target_run_id,
            request_id=request_id,
            decision=decision,
            by=caller.user_id,
            reason=body.reason,
            modified_args=body.modified_args,
        )
        return {"status": "ok", "request_id": request_id, "action": body.action}

    async def cancel(self, caller: Caller, thread_id: str) -> CancelResponse:
        await self._threads.require(caller, thread_id)
        active = await self._rt.find_runs(thread_id=thread_id, active_only=True)
        for run in active:
            if run.tenant == caller.tenant_id:
                await self._rt.request_cancel(run.run_id)
        return CancelResponse(thread_id=thread_id, cancelled=True)

    async def hitl_status(self, caller: Caller, thread_id: str) -> HitlStatus:
        await self._threads.require(caller, thread_id)
        active = await self._rt.find_runs(thread_id=thread_id, active_only=True)
        pending_list: list[dict[str, object]] = []
        for run in active:
            if run.tenant == caller.tenant_id:
                for p in await self._rt.pending_approvals(run.run_id):
                    pending_list.append(
                        {
                            "request_id": p.request_id,
                            "tool_name": p.tool_name,
                            "tool_args": dict(p.args),
                            "risk": p.risk,
                            "summary": p.summary,
                        }
                    )
        status = "waiting" if pending_list else ("running" if active else "idle")
        return HitlStatus(status=status, pending=pending_list)

    async def list_approvals(self, caller: Caller) -> list[ApprovalOut]:
        runs = await self._rt.find_runs(active_only=True)
        approvals: list[ApprovalOut] = []
        for run in runs:
            if run.tenant == caller.tenant_id:
                for p in await self._rt.pending_approvals(run.run_id):
                    approvals.append(
                        ApprovalOut(
                            request_id=p.request_id,
                            thread_id=run.thread_id or "",
                            run_id=run.run_id,
                            tool_name=p.tool_name,
                            tool_args=dict(p.args),
                        )
                    )
        return approvals

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return 0


def chat(container: Container) -> ChatService:
    return ChatService(container, container.get(threads))
