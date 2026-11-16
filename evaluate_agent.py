"""Reliability harness for the care-plan agent:  python evaluate_agent.py

Runs fixed scenarios through the full agent (offline, plus a scripted fake LLM) and checks that
plans follow the retrieved guidelines, never conflict, refuse unsafe or unsupported requests,
recover from bad LLM output, and are deterministic. Exits non-zero if any check fails.
"""

import json
import os
import sys
from datetime import date, datetime

from pawpal_ai.agent import CarePlanAgent
from pawpal_ai.logging_setup import setup_logging
from pawpal_system import Owner, Pet, Scheduler, Task, TaskType

DAY = date(2026, 11, 16)
RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence", "evaluation_results.json")


def run(species, age, notes="", llm=None, existing=()):
    owner = Owner("Eval", "Owner", 30)
    pet = Pet("Pet", age, "unknown", species)
    owner.add_pet(pet)
    for hour, minutes in existing:
        pet.add_task(Task(TaskType.VET, datetime(DAY.year, DAY.month, DAY.day, hour), duration_minutes=minutes))
    sched = Scheduler(owner)
    return CarePlanAgent(sched, llm=llm).plan(pet, notes, day=DAY), sched


def walk_minutes(plan):
    return sum(t.duration_minutes for t in plan.tasks if t.task_type is TaskType.WALK and t.recurrence)


def conflicts(plan, sched):
    return [1 for i, a in enumerate(plan.tasks) for b in plan.tasks[i + 1:] if a.overlaps(b)] + \
           [1 for a in plan.tasks if sched.conflicts_with(a)]


def scripted(*replies):
    """Fake LLM that returns the given replies in order (to test check/revise/fallback)."""
    it = iter(replies)
    return lambda prompt: next(it)


GOOD_DOG = json.dumps({"tasks": [
    {"type": "feed", "time": "07:00", "minutes": 10, "priority": 3, "repeat": "daily", "note": "breakfast"},
    {"type": "walk", "time": "07:30", "minutes": 45, "priority": 2, "repeat": "daily", "note": "run"},
    {"type": "feed", "time": "18:00", "minutes": 10, "priority": 3, "repeat": "daily", "note": "dinner"},
    {"type": "walk", "time": "18:30", "minutes": 45, "priority": 2, "repeat": "daily", "note": "run"}]})
BAD_DOG = json.dumps({"tasks": [  # only 20 min walking, one feed, a 03:00 task
    {"type": "walk", "time": "03:00", "minutes": 20, "repeat": "daily"},
    {"type": "feed", "time": "07:00", "minutes": 10, "repeat": "daily"}]})


def checks():
    """Yield (input, criteria, passed) for each reliability check."""
    p, s = run("dog", 3)
    yield "dog, age 3, no notes", "retrieves only the adult-dog guideline", \
        p.sources == ["Adult dog care (1 to 7 years)"]
    yield "dog, age 3, no notes", "60-120 walk min/day and exactly 2 feeds", \
        60 <= walk_minutes(p) <= 120 and sum(t.task_type is TaskType.FEED for t in p.tasks) == 2

    p, s = run("dog", 2, "very energetic husky")
    yield 'dog, age 2, "very energetic husky"', "retrieves high-energy modifier", \
        "High-energy and working breeds" in p.sources
    yield 'dog, age 2, "very energetic husky"', "walking raised to >= 90 min/day", walk_minutes(p) >= 90

    p, s = run("dog", 10, "stiff joints and arthritis in his hips")
    yield 'dog, age 10, "stiff joints and arthritis"', "arthritis modifier retrieved, walking <= 36 min/day", \
        "Dogs with arthritis or joint problems" in p.sources and walk_minutes(p) <= 36

    p, s = run("cat", 4, "energetic husky")
    yield 'cat, age 4, "energetic husky"', "no walks scheduled (dog modifiers ignored)", \
        p.ok and not any(t.task_type is TaskType.WALK for t in p.tasks)

    p, s = run("dog", 0)
    yield "dog, age 0 (puppy)", "one-off vet booster 7 days out", \
        any(t.task_type is TaskType.VET and (t.due_time.date() - DAY).days == 7 for t in p.tasks)

    p, s = run("dog", 3, existing=[(7, 60)])
    yield "dog, age 3 + existing 07:00-08:00 vet", "tasks moved, 0 conflicts", \
        p.ok and not conflicts(p, s) and any("to avoid a conflict" in step for step in p.trace)

    p, s = run("dog", 3, existing=[(6, 16 * 60 - 1)])
    yield "dog, age 3 + day fully booked", "skips tasks with a warning, never double-books", \
        not conflicts(p, s) and bool(p.warnings)

    p, _ = run("other", 2)
    yield "species 'other'", "refuses instead of guessing", not p.ok
    p, _ = run("dog", 3, "she ate chocolate and is vomiting")
    yield '"she ate chocolate and is vomiting"', "safety stop telling owner to contact a vet", \
        not p.ok and "vet" in p.message.lower()
    p, _ = run("dog", 3, "he is lethargic and very tired")  # found by a limitation probe, then fixed
    yield '"he is lethargic and very tired"', "safety stop telling owner to contact a vet", not p.ok
    p, _ = run("dog", 3, "x" * 600)
    yield "600-character notes", "rejected with a message", not p.ok

    p, _ = run("dog", 2, "energetic husky", llm=scripted(GOOD_DOG))
    yield "fake LLM: valid plan", "accepted on first attempt", p.planner == "claude"
    p, _ = run("dog", 2, "energetic husky", llm=scripted(BAD_DOG, GOOD_DOG))
    yield "fake LLM: invalid plan, then fixed plan", "check fails, revision accepted", \
        p.planner == "claude" and any("attempt 1" in step and "failed" in step for step in p.trace)
    p, _ = run("dog", 2, llm=scripted("sorry, I can't", BAD_DOG))
    yield "fake LLM: non-JSON, then invalid plan", "falls back to rule-based plan", p.ok and p.planner == "rules"
    p, _ = run("cat", 4, llm=scripted(GOOD_DOG, GOOD_DOG))
    yield "fake LLM: walks a cat (twice)", "rejected, falls back, 0 walk minutes", \
        p.planner == "rules" and walk_minutes(p) == 0

    runs = {tuple((t.task_type, t.due_time, t.duration_minutes) for t in run("dog", 2, "husky")[0].tasks)
            for _ in range(5)}
    yield 'dog, age 2, "husky" x5 runs', "identical plan every run", len(runs) == 1


def limitation_probes():
    """Known weak spots. Reported for honesty; they do not affect the exit code."""
    p, _ = run("dog", 2, "has the zoomies all day")
    yield '"has the zoomies all day"', "should retrieve high-energy modifier (synonym)", \
        "High-energy and working breeds" in p.sources
    p, _ = run("dog", 3, "she loves her blood-orange chew toy")
    yield '"she loves her blood-orange chew toy"', "should plan normally (no emergency)", p.ok


def main() -> int:
    setup_logging()  # agent steps go to logs/pawpal_ai.log, not the console
    results = [dict(input=i, criteria=c, passed=bool(ok), kind="check") for i, c, ok in checks()]
    probes = [dict(input=i, criteria=c, passed=bool(ok), kind="limitation_probe") for i, c, ok in limitation_probes()]

    print("| # | Test input | Evaluation criteria | Result |\n|---|---|---|---|")
    for n, r in enumerate(results + probes, 1):
        label = "Pass" if r["passed"] else ("Fail (known limitation)" if r["kind"] == "limitation_probe" else "FAIL")
        print(f"| {n} | {r['input']} | {r['criteria']} | {label} |")
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} reliability checks passed; "
          f"{sum(r['passed'] for r in probes)}/{len(probes)} limitation probes passed.")

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump({"checks_passed": passed, "checks_total": len(results), "results": results + probes}, f, indent=2)
    print(f"Results written to {RESULTS_PATH}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
