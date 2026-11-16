# Reliability evaluation output

`python evaluate_agent.py`

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

18/18 reliability checks passed; 0/2 limitation probes passed.
Results written to <project>/evidence/evaluation_results.json
