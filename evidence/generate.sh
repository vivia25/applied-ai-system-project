#!/usr/bin/env bash
# Regenerates every file in evidence/ from real runs. Usage (from the project root):
#   bash evidence/generate.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
out=evidence

run() {  # run <file> <command...>: write the command and its output to the file
  local file="$1"; shift
  { echo "\$ $*"; echo; "$@" 2>&1 || true; } > "$out/$file"
}

run main_demo.txt              "$PY" main.py
run pytest_output.txt          "$PY" -m pytest -v
run care_plan_husky_conflict.txt "$PY" care_cli.py --demo-conflict --name Luna --species dog --age 2 --yes "energetic husky"
run care_plan_senior_arthritis.txt "$PY" care_cli.py --name Biscuit --species dog --age 11 --yes "stiff joints, limping after long walks"
run care_plan_kitten.txt       "$PY" care_cli.py --name Pip --species cat --age 0 --yes
run care_plan_safety_stop.txt  "$PY" care_cli.py --name Mittens --species cat --age 3 --yes "ate chocolate and is vomiting"
run care_plan_unsupported.txt  "$PY" care_cli.py --name Hopper --species other --age 2 --yes "rabbit"
{ echo "# Reliability evaluation output"; echo; echo '`python evaluate_agent.py`'; echo; "$PY" evaluate_agent.py 2>&1 || true; } > "$out/evaluation_results.md"
# Strip absolute paths so the files read the same on any machine.
sed -i.bak "s#$(pwd)#<project>#g" "$out"/*.txt "$out"/*.md && rm -f "$out"/*.bak
echo "evidence regenerated in $out/"
