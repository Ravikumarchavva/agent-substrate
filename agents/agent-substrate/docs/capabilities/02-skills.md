# 2 · Skills

Skills are **prompt packages** — a `SKILL.md` file containing YAML frontmatter (metadata) plus a Markdown body (procedural instructions for the LLM). They follow the [agentskills.io](https://agentskills.io) open spec.

## SKILL.md structure

```yaml
---
name: code-review          # kebab-case, 1–64 chars
description: Structured code review skill for evaluating code quality...
version: "1.0"
license: MIT
allowed-tools: code_interpreter file_manager   # tools this skill may call
category: development/project
tags: [review, quality, refactor, best-practice]
aliases: [review-code, code-quality, pr-review]
metadata:
  author: agent-framework
---

# Code Review Skill

Use this skill when the user asks you to review code...

## Review Procedure
### Step 1 — Understand Context
...
```

The YAML frontmatter is loaded at startup (cheap — metadata only). The Markdown body is only loaded when the skill is **activated** (the LLM decides it needs it).

## Lifecycle — two-phase loading

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant SM as SkillManager
    participant SL as SkillLoader
    participant Sys as System prompt
    participant LLM as LLM
    participant ST as SkillTool

    Note over SM,SL: Phase 1 — startup (cheap)
    SM->>SL: discover_all(skills_dir)
    loop each skill package
        SL->>SL: walk dir + _parse_frontmatter(SKILL.md)
        SL-->>SM: SkillMetadata(name, description, allowed_tools, …)
    end
    SM->>Sys: inject available_skills_xml() — names + descriptions only
    Note over Sys,LLM: Body NOT loaded yet — keeps prompt small

    Note over LLM,ST: Phase 2 — on demand (lazy)
    LLM->>ST: skills(action="list")
    ST->>SM: list_skills()
    SM-->>ST: list[SkillMetadata]
    ST-->>LLM: names + descriptions

    LLM->>ST: skills(action="activate", name="code-review")
    ST->>SM: activate("code-review")
    SM->>SL: load_skill("code-review")
    SL-->>SM: SkillPackage(metadata, body, scripts, references)
    SM-->>ST: SkillPackage
    ST-->>LLM: full SKILL.md body (to_context_block) + allowed_tools

    Note over LLM: LLM follows the procedure —<br/>tool calls limited to allowed_tools
```

## Three classes

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TB
    classDef cls  fill:#E8EAF6,stroke:#3949AB,color:#1A237E,font-weight:bold
    classDef data fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C
    classDef tool fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef fs   fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2

    ST["SkillTool · tool.py (in Toolbox) — name='skills'<br/>execute(action, name?) → ToolExecutionResult<br/>action=list → names+desc · activate → full body"]:::tool
    SM["SkillManager · _manager.py (app.state.skill_manager)<br/>discover · activate(name) → SkillPackage | None<br/>deactivate_all · inject_into_prompt · list_skills"]:::cls
    SL["SkillLoader · _loader.py<br/>discover_all(dir) → list[SkillMetadata]<br/>load_skill(name) → SkillPackage · _parse_frontmatter"]:::cls
    FS["capabilities/tools/skills/ — 10 SKILL.md packages<br/>code-review · api-testing · web-research · …"]:::fs
    META["SkillMetadata<br/>name · description · version · license<br/>allowed_tools · category · tags · aliases · path"]:::data
    PKG["SkillPackage<br/>metadata · body · scripts · references<br/>to_context_block · list_scripts · read_reference"]:::data

    ST -->|"calls"| SM
    SM -->|"delegates scan"| SL
    SL -->|"reads SKILL.md"| FS
    SM -->|"caches"| META
    META --> PKG
```

### `SkillManager`

The coordinator. Lives on `app.state.skill_manager`.

```python
manager = SkillManager()           # auto_discover=True by default
manager.discover()                 # re-scan (safe to call multiple times)

# Inject into system prompt
system = manager.inject_into_prompt(base_system_prompt)

# Activate on demand (lazy — loads full body)
skill = manager.activate("code-review")   # -> SkillPackage | None
context_block = manager.active_context_block()

# Deactivate between conversations
manager.deactivate_all()
```

### `SkillTool`

The LLM-callable interface registered in the Toolbox. Two actions:

| Action | Effect |
|---|---|
| `list` | Returns all discovered skill names and descriptions |
| `activate` | Loads and returns the full SKILL.md body for the named skill |

### `SkillPackage`

The fully loaded skill. Has helper methods:

```python
skill.to_context_block()    # → Markdown formatted for LLM injection
skill.list_scripts()        # → ["build.sh", "validate.py"]
skill.read_reference("schema.json")   # → file contents | None
```

## Built-in skills

| Skill name | Category | `allowed-tools` |
|---|---|---|
| `api-testing` | development/execution | `http_request`, `code_interpreter` |
| `code-explainer` | development | — |
| `code-review` | development/project | `code_interpreter`, `file_manager` |
| `data-analysis` | analysis | `code_interpreter` |
| `debugging` | development | `code_interpreter` |
| `project-planning` | planning | — |
| `spotify-player` | entertainment | `spotify` |
| `summarization` | writing | — |
| `web-research` | research | `web_search`, `read_url` |
| `writing-assistant` | creative | — |

## Adding a skill

Create a directory under `capabilities/tools/skills/<skill-name>/` with a `SKILL.md`:

```
capabilities/tools/skills/
└── my-skill/
    ├── SKILL.md          ← required
    ├── scripts/          ← optional shell/python helpers
    │   └── run.sh
    └── references/       ← optional reference files
        └── schema.json
```

The `SkillLoader` auto-discovers it at next startup — no registration needed.

### Naming rules (from `SkillMetadata.__post_init__`)

- Lowercase alphanumeric + hyphens only
- Cannot start or end with a hyphen
- No consecutive hyphens (`--`)
- 1–64 characters

```python
# Valid
"code-review", "api-testing", "web-research"

# Invalid — raises ValueError at startup
"Code-Review", "my--skill", "-bad", "good-"
```
