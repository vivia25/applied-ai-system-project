"""Command-line demo of the AI care-plan agent.

    python care_cli.py --name Luna --species dog --age 2 "energetic husky"
    python care_cli.py --demo-conflict --name Rex --species dog --age 3
"""

import argparse
from datetime import date, datetime

from pawpal_ai.agent import CarePlanAgent, apply_plan, claude_llm
from pawpal_ai.logging_setup import LOG_PATH, setup_logging
from pawpal_system import Owner, Pet, Scheduler, Task, TaskType


def main() -> None:
    p = argparse.ArgumentParser(description="Generate a guideline-based care plan for a pet.")
    p.add_argument("notes", nargs="?", default="", help="free-text notes about the pet")
    p.add_argument("--name", default="Buddy")
    p.add_argument("--species", default="dog", choices=["dog", "cat", "other"])
    p.add_argument("--age", type=int, default=3, help="age in whole years (0 = under one)")
    p.add_argument("--demo-conflict", action="store_true",
                   help="pre-book a 07:00-08:00 vet visit so the agent has to work around it")
    p.add_argument("--yes", action="store_true", help="approve the plan without asking")
    args = p.parse_args()

    setup_logging()
    owner = Owner("Demo", "Owner", 30)
    pet = Pet(args.name, args.age, "unknown", args.species)
    owner.add_pet(pet)
    if args.demo_conflict:
        pet.add_task(Task(TaskType.VET, datetime.combine(date.today(), datetime.min.time()).replace(hour=7),
                          duration_minutes=60, description="existing appointment"))
    scheduler = Scheduler(owner)
    llm = claude_llm()
    agent = CarePlanAgent(scheduler, llm=llm)
    print(f"Planner: {'Claude + guideline checks' if llm else 'offline rule-based (no ANTHROPIC_API_KEY)'}\n")

    plan = agent.plan(pet, args.notes)
    print("Agent trace:")
    for step in plan.trace:
        print(f"  - {step}")
    print(f"\n{plan.message}")
    if not plan.ok:
        return
    for t in plan.tasks:
        repeat = t.recurrence.value if t.recurrence else "once"
        print(f"  {t.due_time:%a %b %d %H:%M}-{t.end_time:%H:%M}  {t.task_type.value:<4} {repeat:<6} {plan.notes[id(t)]}")
    for w in plan.warnings:
        print(f"  ! {w}")

    approve = args.yes or input("\nAdd these tasks to the schedule? [y/N] ").strip().lower() == "y"
    if approve:
        print(f"Added {apply_plan(pet, plan)} tasks. Conflicts now: {scheduler.get_conflict_warnings() or 'none'}")
    else:
        print("Nothing added.")
    print(f"(log: {LOG_PATH})")


if __name__ == "__main__":
    main()
