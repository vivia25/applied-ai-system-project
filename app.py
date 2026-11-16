import streamlit as st
from datetime import date, datetime, time

from pawpal_ai.agent import CarePlanAgent, apply_plan, claude_llm
from pawpal_ai.logging_setup import setup_logging
from pawpal_system import Owner, Pet, Recurrence, Scheduler, Task, TaskType

setup_logging()

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

st.title("🐾 PawPal+")

st.markdown(
    """
Welcome to the PawPal+ starter app.

This file is intentionally thin. It gives you a working Streamlit app so you can start quickly,
but **it does not implement the project logic**. Your job is to design the system and build it.

Use this app as your interactive demo once your backend classes/functions exist.
"""
)

with st.expander("Scenario", expanded=True):
    st.markdown(
        """
**PawPal+** is a pet care planning assistant. It helps a pet owner plan care tasks
for their pet(s) based on constraints like time, priority, and preferences.

You will design and implement the scheduling logic and connect it to this Streamlit UI.
"""
    )

with st.expander("What you need to build", expanded=True):
    st.markdown(
        """
At minimum, your system should:
- Represent pet care tasks (what needs to happen, how long it takes, priority)
- Represent the pet and the owner (basic info and preferences)
- Build a plan/schedule for a day that chooses and orders tasks based on constraints
- Explain the plan (why each task was chosen and when it happens)
"""
    )

st.divider()

# Streamlit reruns this script top to bottom on every interaction, so a plain
# `owner = Owner(...)` would be recreated (and emptied) each time. Keeping the
# object in st.session_state, and only creating it when the key is missing,
# makes it persist across reruns.
if "owner" not in st.session_state:
    st.session_state.owner = Owner("Jordan", "Smith", 30)
owner: Owner = st.session_state.owner

st.subheader("Owner")
first_name = st.text_input("First name", value=owner.first_name)
owner.first_name = first_name  # mutate the stored object; it survives reruns

st.subheader("Pets")
col1, col2, col3 = st.columns(3)
with col1:
    pet_name = st.text_input("Pet name", value="Mochi")
with col2:
    species = st.selectbox("Species", ["dog", "cat", "other"])
with col3:
    pet_age = st.number_input("Age (years, 0 = under 1)", min_value=0, max_value=40, value=1)

if st.button("Add pet"):
    owner.add_pet(Pet(pet_name, age=int(pet_age), gender="unknown", animal_type=species))

if owner.pets:
    st.write("Your pets:", ", ".join(f"{p.name} ({p.animal_type}, {p.age}y)" for p in owner.pets))
else:
    st.info("No pets yet. Add one above.")

scheduler = Scheduler(owner)


def task_rows(tasks):
    """Turn tasks into table rows for st.table."""
    return [
        {
            "Time": f"{t.due_time:%H:%M}-{t.end_time:%H:%M}",
            "Pet": t.pet.name,
            "Task": t.task_type.value,
            "Priority": t.priority,
            "Repeats": t.recurrence.value if t.recurrence else "-",
            "Status": "Done" if t.completed else "To do",
        }
        for t in tasks
    ]


st.subheader("Tasks")
if owner.pets:
    pet_choice = st.selectbox("For pet", [p.name for p in owner.pets])
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        task_type = st.selectbox("Type", [t.value for t in TaskType])
    with col2:
        start = st.time_input("Time", value=time(8, 0))
    with col3:
        duration = st.number_input("Minutes", min_value=1, max_value=240, value=20)
    with col4:
        priority = st.selectbox("Priority", [1, 2, 3], index=2)
    with col5:
        repeat = st.selectbox("Repeats", ["none", "daily", "weekly"])

    if st.button("Add task"):
        pet = next(p for p in owner.pets if p.name == pet_choice)
        new_task = Task(
            TaskType(task_type),
            datetime.combine(date.today(), start),
            duration_minutes=int(duration),
            priority=priority,
            recurrence=Recurrence(repeat) if repeat != "none" else None,
        )
        overlaps = scheduler.conflicts_with(new_task)  # check before adding
        pet.add_task(new_task)
        if overlaps:
            clashes = ", ".join(f"{t.task_type.value} for {t.pet.name} at {t.due_time:%H:%M}" for t in overlaps)
            st.warning(f"Added {task_type} for {pet.name}, but it overlaps: {clashes}")
        else:
            st.success(f"Added {task_type} for {pet.name} at {start:%H:%M}")
else:
    st.info("Add a pet first, then you can add tasks.")

st.subheader("🤖 AI care plan")
if owner.pets:
    llm = claude_llm()
    st.caption(
        "Retrieves care guidelines for your pet's species and age, drafts a daily plan, checks it "
        "against those guidelines, and fits it around your existing tasks. "
        + ("Planner: Claude, checked against the guidelines." if llm else
           "Planner: offline rule-based (set ANTHROPIC_API_KEY to use Claude).")
    )
    ai_pet_name = st.selectbox("Plan for", [p.name for p in owner.pets], key="ai_pet")
    ai_notes = st.text_area("Anything we should know? (breed, energy, health)",
                            placeholder="e.g. energetic husky, a bit overweight", max_chars=500)
    if st.button("Draft care plan"):
        ai_pet = next(p for p in owner.pets if p.name == ai_pet_name)
        st.session_state.ai_plan = (ai_pet.name, CarePlanAgent(scheduler, llm=llm).plan(ai_pet, ai_notes))

    if "ai_plan" in st.session_state:
        plan_pet_name, care_plan = st.session_state.ai_plan
        if not care_plan.ok:
            st.error(care_plan.message)
        else:
            st.info(care_plan.message)
            st.write("**Guidelines used:** " + "; ".join(care_plan.sources))
            st.table([
                {
                    "When": f"{t.due_time:%a %H:%M}-{t.end_time:%H:%M}",
                    "Task": t.task_type.value,
                    "Repeats": t.recurrence.value if t.recurrence else "once",
                    "Why": care_plan.notes[id(t)],
                }
                for t in care_plan.tasks
            ])
            for w in care_plan.warnings:
                st.warning(w)
            approve, discard = st.columns(2)
            if approve.button("✅ Approve and add to schedule"):
                plan_pet = next(p for p in owner.pets if p.name == plan_pet_name)
                added = apply_plan(plan_pet, care_plan)
                del st.session_state.ai_plan
                st.session_state.flash = f"Added {added} AI-planned tasks for {plan_pet.name}."
                st.rerun()
            if discard.button("Discard plan"):
                del st.session_state.ai_plan
                st.rerun()
        with st.expander("How the agent reached this plan"):
            for step in care_plan.trace:
                st.text(step)
else:
    st.info("Add a pet first to get an AI care plan.")

st.divider()

if "flash" in st.session_state:  # message saved across the rerun below
    st.success(st.session_state.pop("flash"))

pending = scheduler.get_pending_tasks()
if pending:
    labels = [f"{t.due_time:%a %H:%M} {t.task_type.value} ({t.pet.name})" for t in pending]
    done_label = st.selectbox("Mark a task complete", labels)
    if st.button("Mark complete"):
        next_task = scheduler.complete_task(pending[labels.index(done_label)])
        if next_task:
            st.session_state.flash = (
                f"Done! Next {next_task.recurrence.value} task scheduled for "
                f"{next_task.due_time:%a %b %d, %H:%M}"
            )
        else:
            st.session_state.flash = "Done!"
        st.rerun()

st.divider()

st.subheader("All tasks")
col1, col2 = st.columns(2)
with col1:
    pet_filter = st.selectbox("Show pet", ["All"] + [p.name for p in owner.pets])
with col2:
    status_filter = st.selectbox("Show status", ["All", "To do", "Done"])

shown = scheduler.filter_tasks(
    pet_name=None if pet_filter == "All" else pet_filter,
    completed={"All": None, "To do": False, "Done": True}[status_filter],
)  # already sorted by time
if shown:
    st.caption(f"{len(shown)} task(s), sorted by time")
    st.table(task_rows(shown))
else:
    st.info("No tasks match.")

st.subheader("Conflicts")
conflict_warnings = scheduler.get_conflict_warnings()
if conflict_warnings:
    for warning in conflict_warnings:
        st.warning(warning)
elif pending:
    st.success("No scheduling conflicts.")
else:
    st.info("Nothing to check yet.")

st.divider()

st.subheader("Today's Schedule")
plan = scheduler.get_daily_schedule(date.today())
if plan:
    st.table(task_rows(plan))
    st.caption(f"{len(plan)} task(s), {sum(t.duration_minutes for t in plan)} minutes of pet care today")
else:
    st.info("Nothing scheduled for today.")
