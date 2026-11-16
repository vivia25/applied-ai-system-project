import json
from datetime import date, datetime

import pytest

from pawpal_ai.agent import CarePlanAgent, apply_plan, check_plan, parse_llm_plan, required_limits, rule_plan
from pawpal_ai.retriever import GuidelineStore, tokenize
from pawpal_system import Owner, Pet, Scheduler, Task, TaskType

DAY = date(2026, 11, 16)


@pytest.fixture(scope="module")
def store():
    return GuidelineStore.load()


def setup(species="dog", age=3):
    owner = Owner("T", "O", 30)
    pet = Pet("Rex", age, "male", species)
    owner.add_pet(pet)
    return pet, Scheduler(owner)


def test_tokenize_stems_and_drops_stopwords():
    assert tokenize("The dog is limping") == ["limp"]


@pytest.mark.parametrize("species,age,expected", [
    ("dog", 0, "dog-puppy"), ("dog", 7, "dog-adult"), ("dog", 8, "dog-senior"),
    ("cat", 0, "cat-kitten"), ("cat", 10, "cat-adult"), ("cat", 15, "cat-senior"),
])
def test_life_stage_boundaries(store, species, age, expected):
    assert store.life_stage(species, age)["id"] == expected


def test_unknown_species_has_no_guideline(store):
    assert store.life_stage("rabbit", 2) is None


def test_modifiers_are_species_specific(store):
    assert store.modifiers_for("dog", "hyper border collie")[0][0]["id"] == "dog-high-energy"
    assert store.modifiers_for("cat", "hyper border collie") == []


@pytest.mark.parametrize("species,age,notes", [
    ("dog", 0, ""), ("dog", 3, "husky"), ("dog", 3, "overweight energetic lab"),
    ("dog", 12, "arthritis"), ("cat", 0, ""), ("cat", 5, ""), ("cat", 14, ""),
])
def test_rule_plan_always_passes_its_own_check(store, species, age, notes):
    base = store.life_stage(species, age)
    mods = [d for d, _ in store.modifiers_for(species, notes)]
    assert check_plan(rule_plan(base, mods), species, required_limits(base, mods)) == []


def test_check_catches_bad_plans(store):
    limits = required_limits(store.life_stage("dog", 3), [])
    bad = [{"type": "nap", "time": "25:00", "minutes": 0, "repeat": "hourly"}]
    problems = check_plan(bad, "dog", limits)
    assert any("unknown type" in p for p in problems)
    assert any("feeds" in p for p in problems)


def test_parse_llm_plan_handles_wrapped_json():
    assert parse_llm_plan('Here you go:\n{"tasks": []}\nEnjoy') == []
    with pytest.raises(ValueError):
        parse_llm_plan("no json here")


def test_agent_does_not_modify_schedule_until_approved():
    pet, sched = setup()
    plan = CarePlanAgent(sched).plan(pet, day=DAY)
    assert plan.ok and pet.tasks == []
    assert apply_plan(pet, plan) == len(plan.tasks) == len(pet.tasks)
    assert sched.get_conflict_warnings() == []


def test_agent_moves_tasks_around_existing_booking():
    pet, sched = setup()
    pet.add_task(Task(TaskType.VET, datetime(2026, 11, 16, 7), duration_minutes=60))
    plan = CarePlanAgent(sched).plan(pet, day=DAY)
    assert all(not sched.conflicts_with(t) for t in plan.tasks)
    assert min(t.due_time for t in plan.tasks).hour >= 8


def test_red_flag_stops_planning():
    pet, sched = setup()
    plan = CarePlanAgent(sched).plan(pet, "he has been vomiting since lunch", day=DAY)
    assert not plan.ok and "vet" in plan.message.lower()


def test_llm_exception_falls_back_to_rules():
    def broken(prompt):
        raise ConnectionError("network down")
    pet, sched = setup()
    plan = CarePlanAgent(sched, llm=broken).plan(pet, day=DAY)
    assert plan.ok and plan.planner == "rules"


def test_llm_prompt_contains_retrieved_guideline():
    seen = []
    def spy(prompt):
        seen.append(prompt)
        return json.dumps({"tasks": []})
    pet, sched = setup()
    CarePlanAgent(sched, llm=spy).plan(pet, "energetic husky", day=DAY)
    assert "High-energy and working breeds" in seen[0]
    assert "rejected" in seen[1]  # second attempt gets the problems fed back
