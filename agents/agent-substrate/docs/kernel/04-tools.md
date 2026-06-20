# tools/ — What Agents Can Do

> **Source:** `kernel/tools/tools.py` · `kernel/tools/chain.py` · `kernel/tools/approval.py` · `kernel/tools/skills.py`

Defines the complete tool taxonomy: three execution modes across two axes, wire declarations for LLM providers, sandboxed code chaining, human-in-the-loop approval, and prompt skills.

---

## The Tool Taxonomy

Three Protocols govern all tools. The right one depends on **who executes** and **who declares**:

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef mode fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef proto fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E,font-weight:bold
    classDef result fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef example fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C,font-style:italic
    classDef spec fill:#F3E5F5,stroke:#6A1B9A,stroke-width:1px,color:#4A148C

    LOCAL["LOCAL execution\nTool (Protocol)\nname · description · input_schema\nexecute(ctx, **kwargs)"]:::proto
    HOSTED["PROVIDER execution\nHostedTool (Protocol)\nname · description\nprovider_specs: dict[str, dict]"]:::proto
    PROVDEF["PROVIDER_DEFINED execution\nProviderDefinedTool (Protocol)\nname · description\nprovider_specs · call_types\nhandle_call(call, ctx)"]:::proto

    LOCEX["WebSearchTool\nCalculatorTool\nEmailSenderTool\nTaskManagerTool\n— any capability/tools/ class"]:::example
    HOSEX["OpenAI web_search_preview\nOpenAI code_interpreter\nOpenAI file_search\nAnthropic web_search"]:::example
    PDEX["OpenAI shell\nOpenAI apply_patch\nOpenAI computer_use"]:::example

    TER["ToolExecutionResult\ncall_id · name\ncontent: list[ContentBlock]\nis_error: bool\nstructured_content: dict\ntext (property)"]:::result

    FS["FunctionSpec\nname · description\nparameters: dict\nlazy_schema: bool"]:::spec
    PS["ProviderSpec\nname · provider\nspec: dict (vendor format)"]:::spec

    LOCAL -->|"framework calls execute()"| TER
    PROVDEF -->|"framework calls handle_call()"| TER
    HOSTED -->|"provider runs it\nresult in next turn"| TER

    LOCAL -.->|"spec_of() → "| FS
    HOSTED -.->|"spec_of() → "| PS
    PROVDEF -.->|"spec_of() → "| PS

    LOCAL --- LOCEX
    HOSTED --- HOSEX
    PROVDEF --- PDEX
```

### Dispatch pattern at runtime

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
flowchart TD
    classDef decision fill:#FFF3E0,stroke:#E65100,color:#BF360C,font-weight:bold
    classDef action fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef terminal fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20,font-weight:bold

    LLM["LLM returns tool call"]:::action
    CHECK1{"is_provider_defined_tool(tool)?"}:::decision
    CHECK2{"is_hosted_tool(tool)?"}:::decision
    LOCAL_RUN["await tool.execute(ctx, **args)\n→ ToolExecutionResult"]:::action
    PROV_RUN["await tool.handle_call(call, ctx)\n→ dict (provider output item)"]:::action
    SKIP["Skip local execution\nResult arrives in next LLM turn"]:::terminal

    LLM --> CHECK1
    CHECK1 -->|"yes"| PROV_RUN
    CHECK1 -->|"no"| CHECK2
    CHECK2 -->|"yes"| SKIP
    CHECK2 -->|"no — plain Tool"| LOCAL_RUN

    Note1["Check is_provider_defined FIRST\nboth HostedTool and ProviderDefinedTool\nhave provider_specs"]:::terminal
    CHECK1 -.- Note1
```

**Always check `is_provider_defined_tool` before `is_hosted_tool`** — both have `provider_specs`, but only `ProviderDefinedTool` has `handle_call`.

---

## ToolRisk and Approval

Tools declare a risk level. High and critical risk tools pause execution and ask a human before proceeding.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'actorBkg': '#E8EAF6','actorBorder': '#3949AB','actorTextColor': '#1A237E','activationBkgColor': '#E3F2FD','activationBorderColor': '#1565C0','noteBkgColor': '#FFFDE7','noteBorderColor': '#F57F17','signalColor': '#546E7A','signalTextColor': '#263238','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant Agent
    participant Invoker as "ToolInvoker (L1)"
    participant Handler as "ApprovalHandler"
    participant Human

    Agent->>+Invoker: execute tool "send_email" (ToolRisk.HIGH)

    Invoker->>Invoker: check tool.risk

    alt risk == SAFE
        Invoker->>Invoker: execute immediately
    else risk == HIGH or CRITICAL
        Invoker->>+Handler: request(ApprovalRequest)
        Handler->>Human: "send_email wants to run. Approve?"
        Human-->>Handler: decision
        Handler-->>-Invoker: ApprovalDecision

        alt APPROVED
            Invoker->>Invoker: execute tool
        else DENIED
            Invoker-->>Agent: ToolExecutionResult(is_error=True, "denied by user")
        end
    end

    Invoker-->>-Agent: ToolExecutionResult
```

`ApprovalRequest` is immutable and fully serializable — it can be stored in Postgres and resumed after a restart. `ApprovalHandler` implementations: `WebApprovalHandler` (the `ravi-ui` HITL card), `AutoApprovalHandler` (tests), `CliApprovalHandler` (terminal).

---

## Sandboxed Code-Mode Chaining

Tool chaining lets the LLM write a Python script that calls multiple tools and pipes results between them. The script runs in a Firecracker/K8s sandbox; each tool call crosses the bridge back to the framework-side `ToolInvoker`.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'actorBkg': '#E8EAF6','actorBorder': '#3949AB','actorTextColor': '#1A237E','activationBkgColor': '#E3F2FD','activationBorderColor': '#1565C0','noteBkgColor': '#FFFDE7','noteBorderColor': '#F57F17','signalColor': '#546E7A','signalTextColor': '#263238','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant LLM
    participant Chain as "ToolChainTool (L2)"
    participant Sandbox as "Firecracker Sandbox"
    participant Invoker as "ToolInvoker (L1)"
    participant Tool

    LLM->>+Chain: execute(code="...")
    Chain->>+Sandbox: run(code, prelude_with_bridge)

    loop Each tool call in script (max 50)
        Sandbox->>+Invoker: bridge call: {tool, args}
        Invoker->>+Tool: execute(ctx, **args)
        Tool-->>-Invoker: ToolExecutionResult

        alt result <= 4096 bytes
            Invoker-->>Sandbox: InvocationResult(text, structured)
        else large result
            Invoker->>Invoker: store in ArtifactStore
            Invoker-->>-Sandbox: InvocationResult(artifact_ref, preview)
        end
    end

    Sandbox-->>-Chain: ChainRunResult(output_text, call_trace)
    Chain-->>-LLM: ToolExecutionResult(content)

    Note over Chain: call_trace lists every tool that ran<br/>even on crash — LLM avoids re-sending emails
```

**Key types from `chain.py`:**

| Type | Purpose |
|---|---|
| `ChainPolicy` | Limits: `max_tool_calls=50`, `call_timeout_s=60`, `total_timeout_s=300`, `max_inline_result_bytes=4096` |
| `InvocationResult` | What the sandbox receives back: `status`, `text`, `structured`, `artifact_ref`, `files` |
| `ChainRunResult` | Final outcome: `status`, `output_text`, `call_trace`, `duration_ms` |
| `ChainCallRecord` | One entry per bridged call in the trace: `tool`, `args_digest`, `status`, `duration_ms` |

---

## Skills — Prompt Packages

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef skill fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E,font-weight:bold
    classDef effect fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef src fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C,font-style:italic

    SK["Skill (frozen dataclass)\nname: str\ninstructions: str\ndescription: str\nallowed_tools: tuple[str, ...]\npath: str | None\nversion: str"]:::skill

    SYS["system prompt\n+ instructions appended"]:::effect
    TF["tool filter\nallowed_tools cross-referenced\nagainst agent's ToolRegistry"]:::effect

    MD["capabilities/tools/skills/name/SKILL.md\nYAML frontmatter + prompt body"]:::src
    INLINE["Skill(name=..., instructions=...)"]:::src

    SK --> SYS
    SK --> TF
    MD -.->|"loaded by SkillManager"| SK
    INLINE -.->|"constructed inline"| SK
```

A `Skill` extends an agent's behaviour without modifying its code. When attached to an agent, `instructions` are appended to the effective system prompt and `allowed_tools` limits which tools the skill can use.
