# 🐾 PawPal+ AI: guideline-grounded pet care planning

## Original project

This builds on **PawPal+, my Module 2 project**. PawPal+ is a Streamlit app that helps a busy pet owner plan pet care. It lets you add pets, schedule walks, feedings and vet visits, keep them in time order, repeat daily or weekly routines, and get warnings when two tasks overlap. It had no AI: the owner had to know what care their pet needed and enter every task by hand. The original write-up is in [docs/original_pawpal_readme.md](docs/original_pawpal_readme.md).

## Title and summary

**PawPal+ AI** adds a care-plan agent. You tell it about your pet ("2-year-old dog, energetic husky"), and it does five things:

1. Retrieves care guidelines for that species, age and situation.
2. Drafts a daily routine from those guidelines.
3. Checks the draft against the guidelines' limits and revises it if needed.
4. Fits it around tasks already on your schedule.
5. Shows you the plan, with the reason for each task, to approve before anything is added.

**Why it matters:** new pet owners often don't know how much exercise or how many meals their pet needs, and general-purpose chatbots will confidently invent an answer. This agent only plans from guidelines it can cite, checks its own work, refuses when it has no guideline, and stops to tell you to call a vet if your notes describe an emergency.

## Architecture overview

The full Mermaid diagram is in [diagrams/system_architecture.md](diagrams/system_architecture.md). The UML class diagram is in [diagrams/uml_final.mmd](diagrams/uml_final.mmd).

```
owner notes -> guardrails -> retriever -> agent: plan -> check -> (revise) -> place -> verify -> preview -> owner approves -> schedule
```

| Component | File | Role |
|---|---|---|
| Guardrails | `pawpal_ai/agent.py` | Rejects oversized notes or bad ages. A red-flag word (vomiting, seizure, chocolate...) triggers a safety stop |
| Retriever | `pawpal_ai/retriever.py` | Two-stage retrieval over `data/care_guidelines.json`: a metadata filter picks the life-stage guideline (species + age), then a TF-IDF search finds modifier guidelines that match the notes (high-energy breed, overweight, arthritis) |
| Planner | `pawpal_ai/agent.py` | Claude drafts the plan from the retrieved guidelines only, if `ANTHROPIC_API_KEY` is set. Otherwise a rule-based planner builds it straight from the guidelines |
| Checker / reviser | `check_plan()` | Validates walk minutes, feed count, times, task types, and that cats are never walked. Failures go back to Claude for one revision, then the agent falls back to the rule-based plan |
| Placer / verifier | `CarePlanAgent._place()` | Uses the original `Scheduler.conflicts_with()` to move tasks later in 15-minute steps. It skips a task with a warning if no slot is free within 2 hours, then re-verifies the final plan |
| Human approval | `apply_plan()` + the "Approve" button | Nothing touches the schedule until the owner approves |
| Logging | `logs/pawpal_ai.log` | Every agent step, fallback, safety stop and approval |
| Evaluation | `evaluate_agent.py`, `tests/` | Scenario checks and unit tests (see below) |

## Core PawPal+ system (classes and scheduling algorithms)

The AI agent sits on top of the original object-oriented scheduler in [pawpal_system.py](pawpal_system.py). The UML class diagram, as Mermaid source, is [diagrams/uml_final.mmd](diagrams/uml_final.mmd).

| Class | Key attributes | Key methods |
|---|---|---|
| `Task` | `task_type` (walk/feed/vet), `description`, `due_time`, `duration_minutes`, `priority`, `recurrence`, `completed`, `pet` | `mark_complete()` (creates the next occurrence for recurring tasks), `overlaps()`, `end_time` |
| `Pet` | `name`, `age`, `gender`, `animal_type`, `tasks` | `add_task()`, `remove_task()`, `get_tasks(completed)` |
| `Owner` | `first_name`, `last_name`, `age`, `pets` | `add_pet()`, `get_all_tasks()` (every pet's tasks) |
| `Scheduler` | `owner` (stores no tasks of its own; always reads across **all** pets) | `sort_by_time()`, `filter_tasks()`, `get_daily_schedule()`, `get_pending_tasks()`, `complete_task()`, `find_conflicts()`, `conflicts_with()`, `get_conflict_warnings()` |

**Relationships:** one `Owner` has many `Pet`s, and one `Pet` has many `Task`s. Each `Task` points back to its `Pet`. The `Scheduler` reads everything through `owner.get_all_tasks()`, so there is one source of truth.

**Scheduling algorithms (all work across multiple pets):**

| Feature | Method(s) | Behavior |
|---|---|---|
| Sorting | `Scheduler.sort_by_time()` | By due time; ties broken by higher priority first |
| Filtering | `Scheduler.filter_tasks()` | By pet name, completion status and/or task type, combined |
| Recurring tasks | `Task.mark_complete()`, `Scheduler.complete_task()` | Daily (+1 day) or weekly (+7 days) **from the completion day**, keeping the time of day; completing twice creates no duplicate |
| Conflict detection | `find_conflicts()`, `conflicts_with()`, `get_conflict_warnings()` | Overlapping time ranges (start + duration), same pet or different pets; back-to-back tasks don't conflict; warns, never raises |
| Daily schedule | `get_daily_schedule()` | Today's unfinished tasks in time order |

The AI agent reuses `conflicts_with()` to place its tasks, so it can never create a conflict the scheduler would catch.

### Demo script (`python main.py`)

`main.py` creates one owner, two pets (Rex the dog and Mittens the cat) and six tasks, added out of order on purpose. It then shows each scheduler feature: sorting, filtering, conflict warnings within one pet and across pets, and recurrence. Real output:

```
As added (unsorted)
  14:00  vet   Rex      [todo]
  08:00  walk  Rex      [todo]
  14:00  feed  Rex      [todo]
  09:00  feed  Mittens  [todo]
  18:00  feed  Mittens  [todo]
  08:00  walk  Mittens  [todo]

Sorted by time
  08:00  walk  Rex      [todo]
  08:00  walk  Mittens  [todo]
  09:00  feed  Mittens  [todo]
  14:00  vet   Rex      [todo]
  14:00  feed  Rex      [todo]
  18:00  feed  Mittens  [todo]

Only Mittens
  08:00  walk  Mittens  [todo]
  09:00  feed  Mittens  [todo]
  18:00  feed  Mittens  [todo]

Conflict check
  Warning: walk (Sun 08:00-08:30) overlaps walk (08:00-08:20) for Rex and Mittens
  Warning: vet (Sun 14:00-15:00) overlaps feed (14:00-14:15) for Rex

Completed tasks
  08:00  walk  Rex      [done]

Pending tasks
  08:00  walk  Mittens  [todo]
  09:00  feed  Mittens  [todo]
  14:00  vet   Rex      [todo]
  14:00  feed  Rex      [todo]
  18:00  feed  Mittens  [todo]
  08:00  walk  Rex      [todo]

Today's schedule
  08:00  walk  Mittens  [todo]
  09:00  feed  Mittens  [todo]
  14:00  vet   Rex      [todo]
  14:00  feed  Rex      [todo]
  18:00  feed  Mittens  [todo]
```

## Setup instructions

Requires Python 3.10+.

```bash
git clone https://github.com/vivia25/applied-ai-system-project.git
cd applied-ai-system-project
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python main.py                   # CLI demo of the core scheduler
streamlit run app.py             # web app: add a pet, then use "AI care plan"
python care_cli.py --name Luna --species dog --age 2 "energetic husky"   # command line

# Optional: let Claude draft plans (everything works offline without this)
export ANTHROPIC_API_KEY=your-key-here
```

### Running the tests

```bash
python -m pytest            # all 47 unit tests
python evaluate_agent.py    # AI reliability harness (see Testing summary)
```

**Test coverage:**

- **[tests/test_pawpal.py](tests/test_pawpal.py)** (24 tests, core scheduler):
  - task completion; adding, removing and listing a pet's tasks
  - sorting across pets with priority tie-breaks
  - filtering by pet and status
  - daily and weekly recurrence, including month rollover, late completion and no duplicates
  - conflict detection: same pet, different pets, back-to-back, long tasks, past midnight, no pet
  - empty owners and pets
- **[tests/test_care_agent.py](tests/test_care_agent.py)** (23 tests, AI agent):
  - retrieval by life stage and species
  - plan checking and JSON parsing
  - no schedule changes before approval
  - placing tasks around existing bookings
  - safety stops and LLM-failure fallback

Sample passing output:

```
============================= test session starts ==============================
platform darwin -- Python 3.14.5, pytest-9.1.1, pluggy-1.7.0
rootdir: .../applied-ai-system-project
plugins: anyio-4.15.1
collected 47 items

tests/test_care_agent.py .......................                          [ 48%]
tests/test_pawpal.py ........................                             [100%]

============================== 47 passed in 0.02s ==============================
```

## Sample interactions

Real output from `care_cli.py` in offline mode.

**1. Energetic husky, with an existing 07:00-08:00 vet appointment** (`--demo-conflict`)
```
Agent trace:
  - RETRIEVE: Adult dog care (1 to 7 years), High-energy and working breeds
  - LIMITS: walks 90-180 min/day, feeds 2-2/day
  - PLAN (rules): 4 tasks; CHECK passed
  - PLACE: feed 07:00 -> 08:00 to avoid a conflict
  - PLACE: walk 07:30 -> 08:15 to avoid a conflict
  - VERIFY: 4 tasks placed, 0 conflicts, meets guidelines

Proposed 4 tasks for Luna. Review them, then approve to add.
  Sun 08:00-08:10  feed daily  breakfast
  Sun 08:15-09:00  walk daily  morning walk (adjusted x1.50 for High-energy and working breeds)
  Sun 18:00-18:10  feed daily  dinner
  Sun 18:30-19:15  walk daily  evening walk (adjusted x1.50 for High-energy and working breeds)
Added 4 tasks. Conflicts now: none
```

**2. 11-year-old dog with sore joints**: `"stiff joints, limping after long walks"`
```
  - RETRIEVE: Senior dog care (8 years and older), Dogs with arthritis or joint problems
  - LIMITS: walks 18-36 min/day, feeds 2-3/day
Proposed 6 tasks for Biscuit. Review them, then approve to add.
  Sun Nov 15 07:00-07:10  feed daily  breakfast
  Sun Nov 15 08:00-08:10  walk daily  gentle morning walk (adjusted x0.60 for Dogs with arthritis or joint problems)
  Sun Nov 15 13:00-13:10  walk daily  gentle midday walk (adjusted x0.60 ...)
  Sun Nov 15 18:00-18:10  feed daily  dinner
  Sun Nov 15 18:30-18:40  walk daily  gentle evening walk (adjusted x0.60 ...)
  Sun Nov 22 10:00-11:00  vet  once   senior wellness exam
```

**3. Emergency in the notes (safety guardrail)**: cat, `"ate chocolate and is vomiting"`
```
  - SAFETY STOP: red-flag term 'chocolate'
Your notes mention 'chocolate', which can be an emergency. Please contact a vet now.
PawPal won't make a routine plan for this.
```

**4. Unsupported species**: `--species other`
```
I have no care guidelines for this kind of pet, so I won't guess. Please ask your vet for a routine.
```

In the Streamlit app, the same plan appears as a table with a "Why" column, the guidelines used, any warnings, a "How the agent reached this plan" expander, and **Approve** / **Discard** buttons.

### Reproducible execution evidence

The [evidence/](evidence/) folder holds full, unedited command outputs instead of screenshots. Regenerate all of them with `bash evidence/generate.sh`.

| File | Command |
|---|---|
| [main_demo.txt](evidence/main_demo.txt) | `python main.py` (core scheduler demo) |
| [pytest_output.txt](evidence/pytest_output.txt) | `python -m pytest -v` (47 tests) |
| [evaluation_results.md](evidence/evaluation_results.md) / [.json](evidence/evaluation_results.json) | `python evaluate_agent.py` (reliability table) |
| [care_plan_husky_conflict.txt](evidence/care_plan_husky_conflict.txt) | energetic husky, placed around an existing vet booking |
| [care_plan_senior_arthritis.txt](evidence/care_plan_senior_arthritis.txt) | 11-year-old dog with sore joints |
| [care_plan_kitten.txt](evidence/care_plan_kitten.txt) | kitten (feeds and vet, no walks) |
| [care_plan_safety_stop.txt](evidence/care_plan_safety_stop.txt) | emergency words trigger the safety stop |
| [care_plan_unsupported.txt](evidence/care_plan_unsupported.txt) | unsupported species is refused |

## Design decisions

- **Reuse the original Scheduler, don't replace it.** The agent places tasks with PawPal's own tested `conflicts_with()`, so the AI can't create a conflict the original app would have caught.
- **Two-stage retrieval (metadata filter + TF-IDF).** Species and age must match exactly, because giving a puppy an adult-dog routine is a real mistake, so they are filtered, not searched. The free-text notes are fuzzy, so they get a keyword search. The trade-off is that TF-IDF matches words, not meaning: "zoomies" won't find the high-energy guideline.
- **The guidelines contain hard limits, and every plan is checked against them.** This is what makes the loop agentic and keeps Claude honest: a creative plan that walks a cat or under-exercises a husky is rejected. The trade-off is that limits only exist for what I encoded (walk minutes, feed counts), not for things like portion size.
- **One revision, then a rule-based fallback.** This bounds cost and latency and guarantees a valid plan even when the API is down. The trade-off is that a near-miss LLM plan sometimes gets replaced by the plainer baseline.
- **Preview first, approve second.** The agent can't change your schedule by itself. That's an extra click, but the owner stays in control of their pet's care.
- **Fail closed on safety and coverage.** Red-flag words stop planning, and species without a guideline are refused. This occasionally over-triggers (e.g. "she loves chewing her blood-orange toy" hits *blood*), which I chose over missing a real emergency.
- **Works offline.** No API key is needed to run, test or grade the project, and the Claude path is optional.

## Testing summary

**Summary:** 18/18 reliability checks and 47/47 unit tests pass. The agent handled every bad plan from the scripted fake LLM by rejecting it, revising, or falling back. Its weak spot is **wording it hasn't seen**: retrieval and the safety filter are keyword-based, so synonyms ("zoomies") are missed and innocent words ("blood-orange") can trigger a false alarm. A limitation probe caught that "lethargic" was missing from the safety filter. After I added it, that case moved from fail to pass.

How reliability is measured:

- **Automated scenario checks:** `python evaluate_agent.py` runs the full agent on fixed inputs. It prints the table below and writes the same results as JSON to [evidence/evaluation_results.json](evidence/evaluation_results.json). The exit code is non-zero if any reliability check fails.
- **Fake-LLM tests:** a scripted fake LLM returns good, invalid, non-JSON and unsafe (cat-walking) plans, so the check, revise and fallback loop is tested deterministically, without an API key.
- **Unit tests:** `python -m pytest -q` runs 47 tests, 24 scheduler tests plus 23 for the retriever and agent in [tests/test_care_agent.py](tests/test_care_agent.py).
- **Logging and error handling:** every step is logged to `logs/pawpal_ai.log`, including retrieval, check failures with reasons, revisions, fallbacks, safety stops and approvals. API or parsing errors are caught, logged and handled by the fallback, never shown as a crash.
- **UI check:** Streamlit's `AppTest` drove `app.py` through adding a pet, drafting a plan and approving it, with no exceptions and no conflicts.

### Evaluation results (`python evaluate_agent.py`)

| # | Test input | Evaluation criteria | Result |
|---|---|---|---|
| 1 | dog, age 3, no notes | retrieves only the adult-dog guideline | Pass |
| 2 | dog, age 3, no notes | 60-120 walk min/day and exactly 2 feeds | Pass |
| 3 | dog, age 2, "very energetic husky" | retrieves high-energy modifier | Pass |
| 4 | dog, age 2, "very energetic husky" | walking raised to >= 90 min/day | Pass |
| 5 | dog, age 10, "stiff joints and arthritis" | arthritis modifier retrieved, walking <= 36 min/day | Pass |
| 6 | cat, age 4, "energetic husky" | no walks scheduled (dog modifiers ignored) | Pass |
| 7 | dog, age 0 (puppy) | one-off vet booster 7 days out | Pass |
| 8 | dog, age 3 + existing 07:00-08:00 vet | tasks moved, 0 conflicts | Pass |
| 9 | dog, age 3 + day fully booked | skips tasks with a warning, never double-books | Pass |
| 10 | species 'other' | refuses instead of guessing | Pass |
| 11 | "she ate chocolate and is vomiting" | safety stop telling owner to contact a vet | Pass |
| 12 | "he is lethargic and very tired" | safety stop telling owner to contact a vet | Pass |
| 13 | 600-character notes | rejected with a message | Pass |
| 14 | fake LLM: valid plan | accepted on first attempt | Pass |
| 15 | fake LLM: invalid plan, then fixed plan | check fails, revision accepted | Pass |
| 16 | fake LLM: non-JSON, then invalid plan | falls back to rule-based plan | Pass |
| 17 | fake LLM: walks a cat (twice) | rejected, falls back, 0 walk minutes | Pass |
| 18 | dog, age 2, "husky" x5 runs | identical plan every run | Pass |
| 19 | "has the zoomies all day" | should retrieve high-energy modifier (synonym) | Fail (known limitation) |
| 20 | "she loves her blood-orange chew toy" | should plan normally (no emergency) | Fail (known limitation) |

Rows 1-18 are the reliability checks. Rows 19-20 are **limitation probes**: known weaknesses kept in the report on purpose, so they stay visible. They don't fail the run.

**Not yet tested:** the live Claude path against the real API (only the fake LLM), and combined modifiers. For example, a husky with arthritis gets 1.5 × 0.6 = 0.9× walking, which is plausible but not vet-reviewed.

**What I learned:** writing probes for cases I *expected* to fail found a real safety gap the passing tests never would have. A fake LLM that returns scripted bad answers is the most useful tool for testing an agent's guardrails.

## Reflection

This project taught me that most of what makes an AI feature trustworthy is everything *around* the model: what it's allowed to see, what it gets checked against, what happens when it fails, and who gets the final say. Building the checker first and the planner second turned the LLM into one replaceable part of a system I could test, instead of the thing the whole app depended on.

My reflection on designing the core scheduler with AI (how AI shaped the design, suggestions I accepted and rejected, and how I verified correctness) is in [reflection.md](reflection.md). My responsible-AI reflection covers how I collaborated with AI, a suggestion I accepted and one I rejected, and the system's limitations. It is in [model_card.md](model_card.md).

> ⚠️ PawPal+ AI gives general routines from a small set of guidelines. It is not veterinary advice.
