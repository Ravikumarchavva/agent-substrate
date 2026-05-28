#!/usr/bin/env python3
import os
import shutil
import re
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src" / "ravi"
TESTS_DIR = REPO_ROOT / "tests"

# Moves configuration
# Format: (old_relative_to_src, new_relative_to_src)
moves = [
    # L1 Fabric
    ("kernel/runtime/_local.py", "fabric/runtime/local.py"),
    ("kernel/runtime/_base.py", "fabric/runtime/base.py"),
    ("kernel/runtime/_dispatcher.py", "fabric/runtime/dispatcher.py"),
    ("kernel/runtime/_mailbox.py", "fabric/runtime/mailbox.py"),
    ("kernel/runtime/_supervisor.py", "fabric/runtime/supervisor.py"),
    ("kernel/runtime/_saga.py", "fabric/saga.py"),
    ("kernel/runtime/_resource_lock.py", "fabric/locks.py"),
    ("kernel/runtime/_checkpoint.py", "fabric/checkpoint.py"),
    ("kernel/runtime/_client_channel.py", "fabric/channel.py"),
    ("kernel/agents/actor.py", "fabric/actors/actor.py"),
    ("kernel/agent_catalog", "fabric/catalog"),
    ("kernel/memory/unbounded_memory.py", "fabric/memory/unbounded.py"),
    ("kernel/storage/local.py", "fabric/storage/local.py"),
    ("extensions/control_plane", "fabric/control_plane"),
    ("extensions/events", "fabric/events"),
    ("extensions/metadata", "fabric/metadata"),
    ("extensions/storage", "fabric/storage"),
    ("extensions/tools", "fabric/tools"),
    ("extensions/runtime", "fabric/runtime"),

    # L2 Reasoning
    ("kernel/hooks.py", "reasoning/hooks/manager.py"),
    ("kernel/execution/pipeline.py", "reasoning/middleware/pipeline.py"),
    ("extensions/agents/assistant", "reasoning/agents/assistant"),
    ("extensions/context", "reasoning/memory/context"),
    ("extensions/memory/session_manager.py", "reasoning/memory/session.py"),
    ("extensions/memory/_lineage.py", "reasoning/memory/lineage.py"),
    ("extensions/guardrails", "reasoning/guardrails"),
    ("extensions/middleware", "reasoning/middleware"),
    ("extensions/structured", "reasoning/structured"),
    ("extensions/extraction", "reasoning/extraction"),
    ("extensions/llm", "reasoning/llm"),

    # L3 Orchestration
    ("extensions/agents/orchestrator", "orchestration/agents/orchestrator"),
    ("extensions/agents/flow", "orchestration/agents/flow"),
    ("extensions/agents/user_proxy", "orchestration/agents/proxy"),
    ("extensions/agents/graph", "orchestration/agents/graph"),
    ("extensions/pipelines", "orchestration/workflows"),

    # L4 Guardrails
    ("kernel/safeguards", "guardrails/mutation"),
    ("kernel/governance", "guardrails/governance"),
    ("kernel/economic", "guardrails/economic"),
    ("kernel/observability/_killswitch.py", "guardrails/killswitch.py"),
    ("kernel/semantics", "guardrails/semantic"),
    ("extensions/safeguards", "guardrails/mutation"),
    ("extensions/governance", "guardrails/governance"),
    ("extensions/economic", "guardrails/economic"),
    ("extensions/semantics", "guardrails/semantic"),
    ("extensions/trust", "guardrails/trust"),
    ("extensions/resilience", "guardrails/resilience"),

    # L5 Platform
    ("kernel/observability/_spans.py", "platform/observability/spans.py"),
    ("kernel/observability/_replay.py", "platform/observability/replay.py"),
    ("kernel/scheduler", "platform/scheduling"),
    ("kernel/ranking", "platform/ranking"),
    ("evals", "platform/evals"),
    ("extensions/rag", "platform/rag"),
    ("extensions/batch", "platform/batch"),
    ("extensions/scheduler", "platform/scheduling"),
    ("extensions/ranking", "platform/ranking"),
    ("extensions/observability", "platform/observability"),
]

# Explicit mapping of moved submodules for import rewriting
import_replacements = [
    # kernel.runtime submodules to fabric/fabric.runtime
    (r"ravi\.kernel\.runtime\._local", "ravi.fabric.runtime.local"),
    (r"ravi\.kernel\.runtime\._base", "ravi.fabric.runtime.base"),
    (r"ravi\.kernel\.runtime\._dispatcher", "ravi.fabric.runtime.dispatcher"),
    (r"ravi\.kernel\.runtime\._mailbox", "ravi.fabric.runtime.mailbox"),
    (r"ravi\.kernel\.runtime\._supervisor", "ravi.fabric.runtime.supervisor"),
    (r"ravi\.kernel\.runtime\._saga", "ravi.fabric.saga"),
    (r"ravi\.kernel\.runtime\._resource_lock", "ravi.fabric.locks"),
    (r"ravi\.kernel\.runtime\._checkpoint", "ravi.fabric.checkpoint"),
    (r"ravi\.kernel\.runtime\._client_channel", "ravi.fabric.channel"),
    (r"ravi\.kernel\.agents\.actor", "ravi.fabric.actors.actor"),
    (r"ravi\.kernel\.agent_catalog", "ravi.fabric.catalog"),
    (r"ravi\.kernel\.memory\.unbounded_memory", "ravi.fabric.memory.unbounded"),
    (r"ravi\.kernel\.storage\.local", "ravi.fabric.storage.local"),
    (r"ravi\.extensions\.control_plane", "ravi.fabric.control_plane"),
    (r"ravi\.extensions\.events", "ravi.fabric.events"),
    (r"ravi\.extensions\.metadata", "ravi.fabric.metadata"),
    (r"ravi\.extensions\.storage", "ravi.fabric.storage"),
    (r"ravi\.extensions\.tools", "ravi.fabric.tools"),
    (r"ravi\.extensions\.runtime", "ravi.fabric.runtime"),

    # L2 Reasoning
    (r"ravi\.kernel\.hooks", "ravi.reasoning.hooks.manager"),
    (r"ravi\.kernel\.execution\.pipeline", "ravi.reasoning.middleware.pipeline"),
    (r"ravi\.extensions\.agents\.assistant", "ravi.reasoning.agents.assistant"),
    (r"ravi\.extensions\.context", "ravi.reasoning.memory.context"),
    (r"ravi\.extensions\.memory\.session_manager", "ravi.reasoning.memory.session"),
    (r"ravi\.extensions\.memory\._lineage", "ravi.reasoning.memory.lineage"),
    (r"ravi\.extensions\.guardrails", "ravi.reasoning.guardrails"),
    (r"ravi\.extensions\.middleware", "ravi.reasoning.middleware"),
    (r"ravi\.extensions\.structured", "ravi.reasoning.structured"),
    (r"ravi\.extensions\.extraction", "ravi.reasoning.extraction"),
    (r"ravi\.extensions\.llm", "ravi.reasoning.llm"),

    # L3 Orchestration
    (r"ravi\.extensions\.agents\.orchestrator", "ravi.orchestration.agents.orchestrator"),
    (r"ravi\.extensions\.agents\.flow", "ravi.orchestration.agents.flow"),
    (r"ravi\.extensions\.agents\.user_proxy", "ravi.orchestration.agents.proxy"),
    (r"ravi\.extensions\.agents\.graph", "ravi.orchestration.agents.graph"),
    (r"ravi\.extensions\.pipelines", "ravi.orchestration.workflows"),

    # L4 Guardrails
    (r"ravi\.kernel\.safeguards", "ravi.guardrails.mutation"),
    (r"ravi\.kernel\.governance", "ravi.guardrails.governance"),
    (r"ravi\.kernel\.economic", "ravi.guardrails.economic"),
    (r"ravi\.kernel\.observability\._killswitch", "ravi.guardrails.killswitch"),
    (r"ravi\.kernel\.semantics", "ravi.guardrails.semantic"),
    (r"ravi\.extensions\.safeguards", "ravi.guardrails.mutation"),
    (r"ravi\.extensions\.governance", "ravi.guardrails.governance"),
    (r"ravi\.extensions\.economic", "ravi.guardrails.economic"),
    (r"ravi\.extensions\.semantics", "ravi.guardrails.semantic"),
    (r"ravi\.extensions\.trust", "ravi.guardrails.trust"),
    (r"ravi\.extensions\.resilience", "ravi.guardrails.resilience"),

    # L5 Platform
    (r"ravi\.kernel\.observability\._spans", "ravi.platform.observability.spans"),
    (r"ravi\.kernel\.observability\._replay", "ravi.platform.observability.replay"),
    (r"ravi\.kernel\.scheduler", "ravi.platform.scheduling"),
    (r"ravi\.kernel\.ranking", "ravi.platform.ranking"),
    (r"ravi\.evals", "ravi.platform.evals"),
    (r"ravi\.extensions\.rag", "ravi.platform.rag"),
    (r"ravi\.extensions\.batch", "ravi.platform.batch"),
    (r"ravi\.extensions\.scheduler", "ravi.platform.scheduling"),
    (r"ravi\.extensions\.ranking", "ravi.platform.ranking"),
    (r"ravi\.extensions\.observability", "ravi.platform.observability"),
]

# Specific symbols imported from ravi.kernel.runtime that must now be imported from ravi.fabric or specific modules
symbol_replacements = [
    # Symbol, Old Module, New Module
    ("LocalRuntime", "ravi.kernel.runtime", "ravi.fabric.runtime.local"),
    ("BaseRuntime", "ravi.kernel.runtime", "ravi.fabric.runtime.base"),
    ("Mailbox", "ravi.kernel.runtime", "ravi.fabric.runtime.mailbox"),
    ("Dispatcher", "ravi.kernel.runtime", "ravi.fabric.runtime.dispatcher"),
    ("Supervisor", "ravi.kernel.runtime", "ravi.fabric.runtime.supervisor"),
    ("SagaCoordinator", "ravi.kernel.runtime", "ravi.fabric.saga"),
    ("SagaRecord", "ravi.kernel.runtime", "ravi.fabric.saga"),
    ("SagaStep", "ravi.kernel.runtime", "ravi.fabric.saga"),
    ("SagaFailedError", "ravi.kernel.runtime", "ravi.fabric.saga"),
    ("ResourceLockManager", "ravi.kernel.runtime", "ravi.fabric.locks"),
    ("LockHandle", "ravi.kernel.runtime", "ravi.fabric.locks"),
    ("LockMode", "ravi.kernel.runtime", "ravi.fabric.locks"),
    ("CheckpointStatus", "ravi.kernel.runtime", "ravi.fabric.checkpoint"),
    ("CheckpointStore", "ravi.kernel.runtime", "ravi.fabric.checkpoint"),
    ("InMemoryCheckpointStore", "ravi.kernel.runtime", "ravi.fabric.checkpoint"),
    ("RunCheckpoint", "ravi.kernel.runtime", "ravi.fabric.checkpoint"),
    ("ClientWriteChannel", "ravi.kernel.runtime", "ravi.fabric.channel"),
    ("ClientFrame", "ravi.kernel.runtime", "ravi.fabric.channel"),
    ("WriteLane", "ravi.kernel.runtime", "ravi.fabric.channel"),
    ("ActorAgent", "ravi.kernel.agents", "ravi.fabric.actors.actor"),
]

def make_dirs():
    print("Creating new directories...")
    new_dirs = [
        "fabric/runtime", "fabric/actors", "fabric/catalog", "fabric/memory", "fabric/storage", "fabric/control_plane", "fabric/events", "fabric/metadata", "fabric/tools",
        "reasoning/hooks", "reasoning/middleware", "reasoning/agents/assistant", "reasoning/memory/context", "reasoning/memory/session", "reasoning/memory/lineage", "reasoning/guardrails", "reasoning/structured", "reasoning/extraction", "reasoning/llm",
        "orchestration/agents/orchestrator", "orchestration/agents/flow", "orchestration/agents/proxy", "orchestration/agents/graph", "orchestration/workflows",
        "guardrails/mutation", "guardrails/governance", "guardrails/economic", "guardrails/semantic", "guardrails/trust", "guardrails/resilience",
        "platform/observability", "platform/scheduling", "platform/ranking", "platform/evals", "platform/rag", "platform/batch"
    ]
    for d in new_dirs:
        path = SRC_DIR / d
        path.mkdir(parents=True, exist_ok=True)
        # Touch __init__.py if it doesn't exist
        init_file = path / "__init__.py"
        if not init_file.exists():
            init_file.write_text("from __future__ import annotations\n", encoding="utf-8")

def execute_moves():
    print("Moving files...")
    for old_rel, new_rel in moves:
        old_path = SRC_DIR / old_rel
        new_path = SRC_DIR / new_rel
        if not old_path.exists():
            print(f"Warning: Source path {old_path} does not exist. Skipping.")
            continue
        
        # If destination exists and is a directory or file, remove it first to avoid collision
        if new_path.exists():
            if new_path.is_dir():
                shutil.rmtree(new_path)
            else:
                new_path.unlink()
        
        # Ensure parent exists
        new_path.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"Moving {old_path.relative_to(REPO_ROOT)} -> {new_path.relative_to(REPO_ROOT)}")
        shutil.move(str(old_path), str(new_path))

def update_imports():
    print("Rewriting imports across the codebase...")
    py_files = list(SRC_DIR.rglob("*.py")) + list(TESTS_DIR.rglob("*.py"))
    
    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        original_content = content
        
        # 1. Apply absolute module/package string replacements
        for pattern, replacement in import_replacements:
            # Matches: from ravi.kernel.runtime._local ...
            content = re.sub(pattern, replacement, content)
        
        # 2. Apply symbol-specific import changes
        for symbol, old_module, new_module in symbol_replacements:
            if symbol in content:
                regex_single = rf"from\s+{re.escape(old_module)}\s+import\s+{re.escape(symbol)}\b"
                content = re.sub(regex_single, f"from {new_module} import {symbol}", content)
                
                lines = content.splitlines()
                new_lines = []
                in_multiline_import = False
                multiline_module = None
                multiline_buffer = []
                
                for line in lines:
                    if not in_multiline_import:
                        m = re.match(rf"^\s*from\s+({re.escape(old_module)})\s+import\s+(.+)$", line)
                        if m:
                            import_parts = m.group(2)
                            if "(" in import_parts:
                                in_multiline_import = True
                                multiline_module = m.group(1)
                                multiline_buffer = [line]
                                continue
                            else:
                                symbols = [s.strip() for s in import_parts.split(",")]
                                if symbol in symbols:
                                    remaining = [s for s in symbols if s != symbol and s != ""]
                                    if remaining:
                                        new_lines.append(f"from {old_module} import " + ", ".join(remaining))
                                    new_lines.append(f"from {new_module} import {symbol}")
                                else:
                                    new_lines.append(line)
                        else:
                            new_lines.append(line)
                    else:
                        multiline_buffer.append(line)
                        if ")" in line:
                            in_multiline_import = False
                            full_import = "\n".join(multiline_buffer)
                            m_symbols = re.search(r"\((.*?)\)", full_import, re.DOTALL)
                            if m_symbols:
                                symbols = [s.strip() for s in m_symbols.group(1).replace("\n", "").split(",")]
                                if symbol in symbols:
                                    remaining = [s for s in symbols if s != symbol and s != ""]
                                    if remaining:
                                        new_lines.append(f"from {multiline_module} import (\n    " + ",\n    ".join(remaining) + "\n)")
                                    new_lines.append(f"from {new_module} import {symbol}")
                                else:
                                    new_lines.extend(multiline_buffer)
                            else:
                                new_lines.extend(multiline_buffer)
                            multiline_buffer = []
                content = "\n".join(new_lines) + "\n"
        
        if content != original_content:
            py_file.write_text(content, encoding="utf-8")

def remove_empty_dirs():
    print("Deleting empty/moved source directories...")
    extensions_dir = SRC_DIR / "extensions"
    if extensions_dir.exists():
        print(f"Removing extensions directory: {extensions_dir.relative_to(REPO_ROOT)}")
        shutil.rmtree(extensions_dir)
        
    kernel_empty = ["safeguards", "governance", "economic", "semantics", "scheduler", "ranking"]
    for k in kernel_empty:
        kp = SRC_DIR / "kernel" / k
        if kp.exists():
            print(f"Removing moved kernel directory: {kp.relative_to(REPO_ROOT)}")
            shutil.rmtree(kp)

def main():
    print("--- starting migration ---")
    make_dirs()
    execute_moves()
    update_imports()
    remove_empty_dirs()
    print("--- migration complete ---")

if __name__ == "__main__":
    main()
