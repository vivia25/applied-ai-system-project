"""Care-plan agent: retrieve guidelines -> plan -> check -> revise -> place -> verify.

The agent never adds tasks on its own. It returns a CarePlan preview; the owner approves it
with apply_plan(). Every step is recorded in CarePlan.trace and in the log file.

Planning uses Claude when an LLM function is available, otherwise a deterministic rule-based
planner built directly from the retrieved guidelines. Either way the plan must pass check_plan()
against the retrieved guideline limits. A failing LLM plan gets one revision attempt with the
problems fed back, then the agent falls back to the rule-based plan.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable, Dict, List, Optional

from pawpal_ai.retriever import GuidelineStore
from pawpal_system import Pet, Recurrence, Scheduler, Task, TaskType

logger = logging.getLogger("pawpal.agent")

LLM_MODEL = os.environ.get("PAWPAL_MODEL", "claude-haiku-5-5")
DAY_START, DAY_END = time(6, 0), time(22, 0)
SHIFT_STEP = timedelta(minutes=15)
MAX_SHIFTS = 8  # look up to 2 hours later for a free slot
MAX_TASKS = 10
MAX_NOTES_CHARS = 500

# Words that suggest an emergency. The agent will not plan around these; it tells the owner to call a vet.
RED_FLAGS = re.compile(
    r"\b(vomit\w*|bleed\w*|blood\w*|seizure\w*|poison\w*|chok\w*|collaps\w*|chocolate|xylitol|"
    r"letharg\w*|not eating|won'?t eat|can'?t breathe|unconscious|swallowed)\b",
    re.IGNORECASE,
)

LLMFn = Callable[[str], str]


@dataclass
class CarePlan:
    ok: bool
    message: str = ""
    sources: List[str] = field(default_factory=list)
    planner: str = ""  # "claude" or "rules"
    tasks: List[Task] = field(default_factory=list)
    notes: Dict[int, str] = field(default_factory=dict)  # id(task) -> why
    warnings: List[str] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)


def claude_llm() -> Optional[LLMFn]:
    """Return a function that calls Claude, or None if no API key / SDK is available."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("ANTHROPIC_API_KEY set but the anthropic package is not installed")
        return None
    client = anthropic.Anthropic()

    def call(prompt: str) -> str:
        msg = client.messages.create(
            model=LLM_MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}]
        )
        return "".join(b.text for b in msg.content if b.type == "text")

    return call


# ---------------------------------------------------------------- planning

def _walk_factor(modifiers: List[Dict]) -> float:
    factor = 1.0
    for m in modifiers:
        factor *= m.get("walk_factor", 1.0)
    return min(max(factor, 0.5), 2.0)


def required_limits(base: Dict, modifiers: List[Dict]) -> Dict[str, int]:
    """Guideline limits after modifiers are applied."""
    f = _walk_factor(modifiers)
    lim = base["limits"]
    return {
        "min_walk_minutes": math.floor(lim["min_walk_minutes"] * f),
        "max_walk_minutes": math.ceil(lim["max_walk_minutes"] * f),
        "min_feeds": lim["min_feeds"],
        "max_feeds": lim["max_feeds"],
    }


def rule_plan(base: Dict, modifiers: List[Dict]) -> List[Dict]:
    """Baseline plan from the life-stage guideline, walks scaled by modifiers (rounded up to 5 min)."""
    f = _walk_factor(modifiers)
    plan = []
    for item in base["plan"]:
        item = dict(item)
        if item["type"] == "walk" and f != 1.0:
            item["minutes"] = min(120, max(5, 5 * math.ceil(item["minutes"] * f / 5)))
            item["note"] += f" (adjusted x{f:.2f} for {', '.join(m['title'] for m in modifiers)})"
        plan.append(item)
    return plan


def llm_prompt(pet: Pet, notes: str, base: Dict, modifiers: List[Dict], limits: Dict,
               problems: Optional[List[str]] = None) -> str:
    guidance = "\n".join(f"- {d['title']}: {d['text']}" for d in [base] + modifiers)
    prompt = (
        f"You are planning daily care for {pet.name}, a {pet.age}-year-old {pet.animal_type}.\n"
        f"Owner notes: {notes or '(none)'}\n\n"
        f"Use ONLY these care guidelines:\n{guidance}\n\n"
        f"Baseline plan from the guidelines:\n{json.dumps(rule_plan(base, modifiers))}\n\n"
        f"Hard limits: total daily walk minutes between {limits['min_walk_minutes']} and "
        f"{limits['max_walk_minutes']}; daily feeds between {limits['min_feeds']} and "
        f"{limits['max_feeds']}; times between 06:00 and 21:30; at most {MAX_TASKS} tasks; "
        "type must be walk, feed or vet; repeat must be daily, weekly or none.\n\n"
        "Adapt the baseline to the owner's notes where the guidelines support it. Reply with "
        'ONLY JSON: {"tasks": [{"type": "walk", "time": "HH:MM", "minutes": 30, "priority": 1-3, '
        '"repeat": "daily", "day_offset": 0, "note": "short reason citing a guideline"}]}'
    )
    if problems:
        prompt += "\n\nYour previous plan was rejected for these reasons; fix them:\n- " + "\n- ".join(problems)
    return prompt


def parse_llm_plan(text: str) -> List[Dict]:
    """Extract the task list from an LLM reply. Raises ValueError if it isn't valid JSON."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in reply")
    data = json.loads(match.group(0))
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        raise ValueError("reply has no 'tasks' list")
    return tasks


# ---------------------------------------------------------------- checking

def _parse_time(value) -> Optional[time]:
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except ValueError:
        return None


def check_plan(plan: List[Dict], species: str, limits: Dict) -> List[str]:
    """Return every way the plan breaks the guideline limits or input rules (empty = passes)."""
    problems = []
    if not plan:
        return ["plan is empty"]
    if len(plan) > MAX_TASKS:
        problems.append(f"too many tasks ({len(plan)} > {MAX_TASKS})")
    walk_total, feeds = 0, 0
    for i, item in enumerate(plan):
        if not isinstance(item, dict):
            problems.append(f"task {i} is not an object")
            continue
        kind = item.get("type")
        if kind not in {t.value for t in TaskType}:
            problems.append(f"task {i}: unknown type {kind!r}")
            continue
        t = _parse_time(item.get("time"))
        if t is None or not (DAY_START <= t <= time(21, 30)):
            problems.append(f"task {i}: time {item.get('time')!r} must be HH:MM between 06:00 and 21:30")
        minutes = item.get("minutes")
        if not isinstance(minutes, int) or not 5 <= minutes <= 180:
            problems.append(f"task {i}: minutes {minutes!r} must be a whole number from 5 to 180")
            minutes = 0
        if item.get("repeat", "none") not in {"daily", "weekly", "none"}:
            problems.append(f"task {i}: repeat {item.get('repeat')!r} is invalid")
        if not isinstance(item.get("day_offset", 0), int) or not 0 <= item.get("day_offset", 0) <= 30:
            problems.append(f"task {i}: day_offset must be 0-30")
        daily = item.get("repeat") == "daily" and item.get("day_offset", 0) == 0
        if kind == "walk":
            if species == "cat":
                problems.append(f"task {i}: cats should not be scheduled for walks")
            if daily:
                walk_total += minutes
        if kind == "feed" and daily:
            feeds += 1
    if species != "cat" and not limits["min_walk_minutes"] <= walk_total <= limits["max_walk_minutes"]:
        problems.append(f"daily walking is {walk_total} min; guideline range is "
                        f"{limits['min_walk_minutes']}-{limits['max_walk_minutes']} min")
    if not limits["min_feeds"] <= feeds <= limits["max_feeds"]:
        problems.append(f"{feeds} daily feeds; guideline range is {limits['min_feeds']}-{limits['max_feeds']}")
    return problems


# ---------------------------------------------------------------- the agent

class CarePlanAgent:
    def __init__(self, scheduler: Scheduler, store: Optional[GuidelineStore] = None,
                 llm: Optional[LLMFn] = None):
        self.scheduler = scheduler
        self.store = store or GuidelineStore.load()
        self.llm = llm

    def plan(self, pet: Pet, notes: str = "", day: Optional[date] = None) -> CarePlan:
        day = day or date.today()
        result = CarePlan(ok=False)
        log = lambda msg: (result.trace.append(msg), logger.info("[%s] %s", pet.name, msg))

        # 1. Guardrails on input
        notes = "".join(ch for ch in (notes or "") if ch.isprintable()).strip()
        if len(notes) > MAX_NOTES_CHARS:
            result.message = f"Notes are too long (max {MAX_NOTES_CHARS} characters)."
            log(f"REJECT: {result.message}")
            return result
        if not isinstance(pet.age, int) or not 0 <= pet.age <= 40:
            result.message = f"{pet.name}'s age ({pet.age}) doesn't look right; please fix it first."
            log(f"REJECT: {result.message}")
            return result
        flag = RED_FLAGS.search(notes)
        if flag:
            result.message = (f"Your notes mention '{flag.group(0)}', which can be an emergency. "
                              "Please contact a vet now. PawPal won't make a routine plan for this.")
            log(f"SAFETY STOP: red-flag term {flag.group(0)!r}")
            return result

        # 2. Retrieve
        base = self.store.life_stage(pet.animal_type, pet.age)
        if base is None:
            kind = "this kind of pet" if pet.animal_type == "other" else f"a {pet.age}-year-old {pet.animal_type}"
            result.message = (f"I have no care guidelines for {kind}, "
                              "so I won't guess. Please ask your vet for a routine.")
            log(f"RETRIEVE: no guideline for {pet.animal_type} age {pet.age}; stopping")
            return result
        modifiers = [d for d, _ in self.store.modifiers_for(pet.animal_type, notes)]
        result.sources = [d["title"] for d in [base] + modifiers]
        limits = required_limits(base, modifiers)
        log(f"RETRIEVE: {', '.join(result.sources)}")
        log(f"LIMITS: walks {limits['min_walk_minutes']}-{limits['max_walk_minutes']} min/day, "
            f"feeds {limits['min_feeds']}-{limits['max_feeds']}/day")

        # 3. Plan, 4. check, 5. revise
        proposal, result.planner = None, "rules"
        if self.llm is not None:
            problems = None
            for attempt in (1, 2):
                try:
                    candidate = parse_llm_plan(self.llm(llm_prompt(pet, notes, base, modifiers, limits, problems)))
                    problems = check_plan(candidate, pet.animal_type, limits)
                except Exception as e:  # API error, bad JSON, etc.
                    candidate, problems = None, [f"could not use reply: {e}"]
                    logger.warning("LLM planning failed on attempt %d: %r", attempt, e)
                if not problems:
                    proposal, result.planner = candidate, "claude"
                    log(f"PLAN (claude, attempt {attempt}): {len(candidate)} tasks; CHECK passed")
                    break
                log(f"CHECK failed on claude attempt {attempt}: {'; '.join(problems)}")
            if proposal is None:
                log("REVISE: falling back to the rule-based plan from the guidelines")
        if proposal is None:
            proposal = rule_plan(base, modifiers)
            problems = check_plan(proposal, pet.animal_type, limits)
            log(f"PLAN (rules): {len(proposal)} tasks; CHECK {'passed' if not problems else 'failed: ' + '; '.join(problems)}")
            if problems:  # would mean the guideline data itself is inconsistent
                result.message = "Internal check failed on the guideline plan: " + "; ".join(problems)
                return result

        # 6. Place each task, moving it later if it clashes with the existing schedule
        placed: List[Task] = []
        for item in sorted(proposal, key=lambda i: (i.get("day_offset", 0), i["time"])):
            task = self._place(item, day, placed, result, log)
            if task is not None:
                placed.append(task)
                result.notes[id(task)] = item.get("note", "")

        # 7. Verify the placed plan still meets the guidelines and has no conflicts
        final = [{"type": t.task_type.value, "time": f"{t.due_time:%H:%M}", "minutes": t.duration_minutes,
                  "repeat": t.recurrence.value if t.recurrence else "none",
                  "day_offset": (t.due_time.date() - day).days} for t in placed]
        problems = check_plan(final, pet.animal_type, limits)
        clashes = [(a, b) for i, a in enumerate(placed) for b in placed[i + 1:] if a.overlaps(b)]
        clashes += [(a, b) for a in placed for b in self.scheduler.conflicts_with(a)]
        if problems:
            result.warnings.append("After fitting around your schedule the plan falls short: " + "; ".join(problems))
        if clashes:
            result.warnings.append(f"{len(clashes)} conflict(s) remain")
        log(f"VERIFY: {len(placed)} tasks placed, {len(clashes)} conflicts, "
            f"{'meets' if not problems else 'misses'} guidelines")

        result.ok, result.tasks = True, placed
        result.message = f"Proposed {len(placed)} tasks for {pet.name}. Review them, then approve to add."
        return result

    def _place(self, item: Dict, day: date, placed: List[Task], result: CarePlan, log) -> Optional[Task]:
        start = datetime.combine(day + timedelta(days=item.get("day_offset", 0)), _parse_time(item["time"]))
        repeat = item.get("repeat", "none")
        task = Task(
            TaskType(item["type"]), start, description=item.get("note", ""),
            duration_minutes=item["minutes"], priority=int(item.get("priority", 2)),
            recurrence=None if repeat == "none" else Recurrence(repeat),
        )
        for _ in range(MAX_SHIFTS + 1):
            clash = self.scheduler.conflicts_with(task) + [p for p in placed if p.overlaps(task)]
            if not clash:
                if task.due_time != start:
                    log(f"PLACE: {item['type']} {start:%H:%M} -> {task.due_time:%H:%M} to avoid a conflict")
                return task
            if task.end_time.time() > DAY_END or (task.due_time + SHIFT_STEP).date() != start.date():
                break
            task.due_time += SHIFT_STEP
        what = ", ".join(f"{c.task_type.value} at {c.due_time:%H:%M}" for c in clash)
        result.warnings.append(f"Couldn't fit the {item['type']} at {start:%H:%M} (clashes with {what}); skipped it.")
        log(f"PLACE: skipped {item['type']} {start:%H:%M}, no free slot within 2 hours")
        return None


def apply_plan(pet: Pet, plan: CarePlan) -> int:
    """Human-approved step: add the previewed tasks to the pet. Returns how many were added."""
    if not plan.ok:
        return 0
    for task in plan.tasks:
        pet.add_task(task)
    logger.info("[%s] APPLY: owner approved %d tasks", pet.name, len(plan.tasks))
    return len(plan.tasks)
