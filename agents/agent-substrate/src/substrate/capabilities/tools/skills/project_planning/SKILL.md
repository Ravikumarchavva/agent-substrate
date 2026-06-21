---
name: project-planning
description: Project planning skill for breaking down work into milestones, estimating scope, and creating structured implementation plans.
version: "1.0"
license: MIT
allowed-tools: manage_tasks
category: productivity
tags: [plan, project, milestone, timeline, scope, estimate, roadmap, breakdown]
aliases: [plan-project, create-plan, project-roadmap]
metadata:
  author: agent-framework
---

# Project Planning Skill

Use this skill when the user asks to plan a project, create a roadmap, break down work, or estimate scope.

## Planning Procedure

### Step 1 — Define Scope
Clarify with the user:
- What is the goal? (one-sentence project objective)
- What are the constraints? (timeline, budget, team size, tech stack)
- What is out of scope? (explicitly list exclusions)
- What does "done" look like? (acceptance criteria)

### Step 2 — Break Down into Milestones
Split the project into 3-7 milestones:
- Each milestone delivers tangible, testable value
- Milestones are ordered by dependency (what must come first?)
- Early milestones should reduce risk and uncertainty

### Step 3 — Task Decomposition
For each milestone, create actionable tasks:
- Tasks should be completable in 1-4 hours of work
- Each task has a clear definition of done
- Identify dependencies between tasks
- Use `manage_tasks` to create the task list

### Step 4 — Identify Risks
For each milestone:
- What could go wrong?
- What is unknown or uncertain?
- What external dependencies exist?
- Mitigation strategy for each risk

### Step 5 — Create the Plan
Present the plan as:
```
## Project: [Name]
**Goal:** [one sentence]
**Timeline:** [estimated duration]

### Milestone 1: [Name] — [duration estimate]
- [ ] Task 1.1: description
- [ ] Task 1.2: description
Dependencies: none

### Milestone 2: [Name] — [duration estimate]
- [ ] Task 2.1: description
- [ ] Task 2.2: description
Dependencies: Milestone 1

### Risks
| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| ... | High/Med/Low | High/Med/Low | ... |
```

## Best Practices
- Start with the hard/uncertain parts first (reduce risk early)
- Plan for testing and review as explicit tasks, not afterthoughts
- Include buffer time (add 20-30% to estimates)
- Prefer small, frequent deliverables over big-bang releases
- Re-plan when scope changes significantly — don't just add tasks
