# PawPal+ AI Care Plan: System Diagram

```mermaid
flowchart TD
    %% ---------- Input ----------
    Owner([Pet owner]) -->|pet species + age,<br/>free-text notes| UI[app.py Streamlit UI<br/>or care_cli.py]
    UI --> Guard{Input guardrails<br/>notes too long? bad age?<br/>red-flag words e.g. vomiting, chocolate?}
    Guard -- red flag --> Vet[/Safety stop:<br/>contact a vet now/]
    Guard -- invalid --> Reject[/Rejection message/]

    %% ---------- Retrieve ----------
    Guard -- ok --> Retriever[Retriever<br/>pawpal_ai/retriever.py]
    KB[(data/care_guidelines.json<br/>6 life-stage guidelines<br/>3 modifier guidelines)] --> Retriever
    Retriever -->|1. metadata filter: species + age| Base[Life-stage guideline<br/>+ limits]
    Retriever -->|2. TF-IDF on notes| Mods[Matching modifiers<br/>e.g. high-energy, arthritis]
    Base -- none found --> NoGuess[/Refusal: no guideline,<br/>won't guess/]

    %% ---------- Agent loop ----------
    subgraph Agent [CarePlanAgent: pawpal_ai/agent.py]
        direction TB
        Plan{Claude available?}
        Plan -- yes --> LLM[Claude drafts plan<br/>from retrieved guidelines only]
        Plan -- no --> Rules[Rule-based plan<br/>from guideline + modifiers]
        LLM --> Check{check_plan<br/>walk minutes, feed count,<br/>times, task types, cats not walked}
        Check -- fails, 1st try --> Revise[Feed problems back,<br/>Claude revises once]
        Revise --> Check
        Check -- fails twice / API error --> Rules
        Rules --> Check2{check_plan}
        Check -- passes --> Place
        Check2 -- passes --> Place[Place tasks<br/>shift +15 min on conflict,<br/>skip + warn after 2 h]
        Place --> Verify[Verify<br/>0 conflicts? still meets guidelines?]
    end
    Base --> Plan
    Mods --> Plan
    Sched[(pawpal_system.py<br/>Scheduler: existing tasks,<br/>conflicts_with)] --> Place
    Sched --> Verify

    %% ---------- Output + human approval ----------
    Verify --> Preview[/Preview: tasks + why,<br/>guidelines used, warnings,<br/>agent trace/]
    Preview --> Approve{Owner approves?}
    Approve -- yes --> Apply[apply_plan adds tasks<br/>to the pet's schedule]
    Approve -- no --> Discard[/Plan discarded,<br/>schedule unchanged/]
    Apply --> Sched

    %% ---------- Logging ----------
    Guard -.-> Log[(logs/pawpal_ai.log)]
    Agent -.-> Log
    Apply -.-> Log

    %% ---------- Testing / human review ----------
    subgraph Testing [Testing and human review]
        direction TB
        Eval[evaluate_agent.py<br/>18 scenario checks incl.<br/>scripted bad LLM replies]
        Unit[tests/test_care_agent.py<br/>+ tests/test_pawpal.py<br/>47 pytest tests]
        Dev([Developer reviews results<br/>and logs, edits guidelines])
    end
    Eval -- runs full agent --> Agent
    Unit -- tests --> Retriever
    Unit -- tests --> Agent
    Eval --> Dev
    Unit --> Dev
    Log --> Dev
    Dev -- updates --> KB
```

## Reading the diagram

- **Data flow:** owner input, then guardrails, then retrieval (life-stage guideline plus modifiers), then the agent loop (plan, check, revise, place, verify), then a preview, the owner's approval and the schedule.
- **RAG drives the plan:** the retrieved guidelines provide the baseline tasks, the limits the plan is checked against, and the only context Claude is given.
- **Agentic loop:** the agent checks its own draft, sends problems back for one revision, falls back to a safe rule-based plan, and uses the existing `Scheduler` to place tasks around existing bookings.
- **Humans in the loop:** nothing is added until the owner approves. The developer uses the evaluation report, the tests and the log to improve the guidelines.
- **Safe failure:** red-flag symptoms, unsupported species, invalid input and LLM errors each end in a clear message or a fallback, never a guessed plan.
