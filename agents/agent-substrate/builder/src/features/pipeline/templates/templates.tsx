/*  Template data  */
import type { PipelineConfig } from "@/types";
import type { ReactNode } from "react";
import {HomeIcon, Book, Bolt, Database, Map, Scale} from "lucide-react";

interface Template {
  name: string;
  description: string;
  config: PipelineConfig;
  meta: {
    icon: ReactNode;
    color: string;
    tag: string;
  };
}

const TEMPLATES: Template[] = [
  {
    name: "Simple assistant",
    description: "Single agent with memory — the Hello World of agentic workflows",
    config: {
      id: "", name: "Simple assistant", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 0, y: 200 }, config: {} },
        { id: "agent_1", node_type: "agent", label: "Assistant", position: { x: 300, y: 200 }, config: { model: "gpt-4o-mini", system_prompt: "You are a helpful assistant.", max_iterations: 10 } },
        { id: "memory_1", node_type: "memory", label: "Memory", position: { x: 600, y: 200 }, config: { backend: "unbounded" } },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "memory_1", source_handle: "", target_handle: "", edge_type: "agent_memory", label: "" },
      ],
    },
    meta: {
      icon: <HomeIcon size={18} />,
      color: "#a1a1aa",
      tag: "Starter",
    },
  },
  {
    name: "Research agent",
    description: "Agent with web search tool and content safety guardrails",
    config: {
      id: "", name: "Research agent", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 200 }, config: {} },
        { id: "guard_1", node_type: "guardrail", label: "Content filter", position: { x: 250, y: 100 }, config: { guardrail_type: "input", schema_name: "ContentSafetyJudge", pass_field: "is_safe", tripwire: true } },
        { id: "agent_1", node_type: "agent", label: "Researcher", position: { x: 250, y: 230 }, config: { model: "gpt-4o-mini", system_prompt: "You are a research assistant. Search the web for information and provide well-sourced answers.", max_iterations: 15 } },
        { id: "tool_1", node_type: "tool", label: "Web search", position: { x: 500, y: 230 }, config: { tool_name: "web_search", risk: "safe" } },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "guard_1", source_handle: "", target_handle: "", edge_type: "agent_guardrail", label: "" },
        { id: "e3", source: "agent_1", target: "tool_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
      ],
    },
    meta: {
      icon: <Book size={18} />,
      color: "#a1a1aa",
      tag: "Research",
    },
  },
  {
    name: "Customer service",
    description: "Classify intent then route to specialized sub-agents",
    config: {
      id: "", name: "Customer service", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 0, y: 250 }, config: {} },
        { id: "guard_1", node_type: "guardrail", label: "Jailbreak guard", position: { x: 230, y: 200 }, config: { guardrail_type: "input", schema_name: "ContentSafetyJudge", pass_field: "is_safe", tripwire: true } },
        { id: "agent_1", node_type: "agent", label: "Classifier", position: { x: 520, y: 250 }, config: { model: "gpt-4o-mini", system_prompt: "Classify the user intent into: billing, technical, general.", max_iterations: 3 } },
        { id: "cond_1", node_type: "condition", label: "Condition", position: { x: 750, y: 200 }, config: { conditions: [{ expression: 'intent == "billing"', label: "Billing" }, { expression: 'intent == "technical"', label: "Technical" }] } },
        { id: "agent_2", node_type: "agent", label: "Billing agent", position: { x: 1050, y: 120 }, config: { model: "gpt-4o-mini", system_prompt: "You help with billing inquiries.", max_iterations: 10 } },
        { id: "agent_3", node_type: "agent", label: "Tech support", position: { x: 1050, y: 250 }, config: { model: "gpt-4o-mini", system_prompt: "You provide technical support.", max_iterations: 10 } },
        { id: "agent_4", node_type: "agent", label: "General agent", position: { x: 1050, y: 380 }, config: { model: "gpt-4o-mini", system_prompt: "You handle general queries.", max_iterations: 10 } },
        { id: "end_1", node_type: "end", label: "End", position: { x: 520, y: 350 }, config: {} },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "guard_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "guard_1", target: "agent_1", source_handle: "pass", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e3", source: "guard_1", target: "end_1", source_handle: "fail", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e4", source: "agent_1", target: "cond_1", source_handle: "", target_handle: "", edge_type: "router_input", label: "" },
        { id: "e5", source: "cond_1", target: "agent_2", source_handle: "cond-0", target_handle: "", edge_type: "router_route", label: "" },
        { id: "e6", source: "cond_1", target: "agent_3", source_handle: "cond-1", target_handle: "", edge_type: "router_route", label: "" },
        { id: "e7", source: "cond_1", target: "agent_4", source_handle: "else", target_handle: "", edge_type: "router_route", label: "" },
      ],
    },
    meta: {
      icon: <Bolt size={18} />,
      color: "#a1a1aa",
      tag: "Research",
    },
  },
  {
    name: "Data enrichment",
    description: "Pull together data to answer user questions with tools",
    config: {
      id: "", name: "Data enrichment", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 200 }, config: {} },
        { id: "agent_1", node_type: "agent", label: "Data agent", position: { x: 260, y: 188 }, config: { model: "gpt-4o-mini", system_prompt: "You enrich data by using available tools. Answer questions fully.", max_iterations: 15 } },
        { id: "tool_1", node_type: "tool", label: "Web search", position: { x: 520, y: 130 }, config: { tool_name: "web_search", risk: "safe" } },
        { id: "tool_2", node_type: "tool", label: "File search", position: { x: 520, y: 250 }, config: { tool_name: "file_search", risk: "safe" } },
        { id: "memory_1", node_type: "memory", label: "Memory", position: { x: 520, y: 340 }, config: { backend: "redis", ttl: 3600 } },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "tool_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e3", source: "agent_1", target: "tool_2", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e4", source: "agent_1", target: "memory_1", source_handle: "", target_handle: "", edge_type: "agent_memory", label: "" },
      ],
    },
    meta: {
      icon: <Database size={18} />,
      color: "#a1a1aa",
      tag: "Research",
    },
  },
  {
    name: "Planning helper",
    description: "Multi-turn agent for creating work plans with approval gates",
    config: {
      id: "", name: "Planning helper", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 200 }, config: {} },
        { id: "agent_1", node_type: "agent", label: "Planner", position: { x: 260, y: 188 }, config: { model: "gpt-4o", system_prompt: "You create detailed work plans. Always present the plan for approval before finalizing.", max_iterations: 20 } },
        { id: "approval_1", node_type: "approval", label: "Review plan", position: { x: 520, y: 170 }, config: { prompt: "Does this plan look good?" } },
        { id: "agent_2", node_type: "agent", label: "Finalizer", position: { x: 760, y: 130 }, config: { model: "gpt-4o-mini", system_prompt: "Format the approved plan into a clean document.", max_iterations: 5 } },
        { id: "end_1", node_type: "end", label: "End", position: { x: 760, y: 270 }, config: {} },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "approval_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e3", source: "approval_1", target: "agent_2", source_handle: "approve", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e4", source: "approval_1", target: "end_1", source_handle: "reject", target_handle: "", edge_type: "agent_tool", label: "" },
      ],
    },
    meta: {
      icon: <Map size={18} />,
      color: "#a1a1aa",
      tag: "Research",
    },
  },
  {
    name: "Document comparison",
    description: "Analyze and highlight differences across uploaded documents",
    config: {
      id: "", name: "Document comparison", description: "",
      nodes: [
        { id: "start_1", node_type: "start", label: "Start", position: { x: 50, y: 200 }, config: {} },
        { id: "agent_1", node_type: "agent", label: "Triage", position: { x: 260, y: 188 }, config: { model: "gpt-4o-mini", system_prompt: "Classify whether the user wants to compare or ask questions.", max_iterations: 5 } },
        { id: "cond_1", node_type: "condition", label: "If / else", position: { x: 480, y: 170 }, config: { conditions: [{ expression: 'task == "compare"', label: "Compare" }] } },
        { id: "agent_2", node_type: "agent", label: "Comparison agent", position: { x: 740, y: 130 }, config: { model: "gpt-4o", system_prompt: "Compare the provided documents and highlight key differences.", max_iterations: 15 } },
        { id: "agent_3", node_type: "agent", label: "Q&A agent", position: { x: 740, y: 280 }, config: { model: "gpt-4o-mini", system_prompt: "Answer questions about the documents.", max_iterations: 10 } },
      ],
      edges: [
        { id: "e1", source: "start_1", target: "agent_1", source_handle: "", target_handle: "", edge_type: "agent_tool", label: "" },
        { id: "e2", source: "agent_1", target: "cond_1", source_handle: "", target_handle: "", edge_type: "router_input", label: "" },
        { id: "e3", source: "cond_1", target: "agent_2", source_handle: "cond-0", target_handle: "", edge_type: "router_route", label: "" },
        { id: "e4", source: "cond_1", target: "agent_3", source_handle: "else", target_handle: "", edge_type: "router_route", label: "" },
      ],
    },
    meta: {
      icon: <Scale size={18} />,
      color: "#a1a1aa",
      tag: "Research",
    },
  },
];

export { TEMPLATES };

export type { Template };