"""Prompt construction for the OpenAI-compatible directive interpreter.

The system prompt pins the exact ``directive_interpretation`` shape and carries
few-shot examples for all six directive types, paraphrase handling, relative
percentages, time windows, distractors, and combined notes.

The top-level shape is a JSON **object** with a ``directives`` array, which is
what providers' ``response_format: {"type": "json_object"}`` requires. Requesting
a bare array while forcing JSON-object mode makes models emit only the first
directive, so the object wrapper is deliberate.
"""

from __future__ import annotations

from app.schemas import BatterySpec

SYSTEM_PROMPT = """You are GridWise, a deterministic interpreter that translates energy-operator notes into structured battery/grid/solar directives.

Return ONLY a JSON object (no prose, no markdown fences) with a single key "directives" whose value is an array. The array MUST contain exactly one directive object per operator note, in note order. If there are 3 notes, return 3 objects.

Shape:
{"directives": [
  {
    "note_index": <zero-based index matching the note order>,
    "applies": <true for every directive except no_op, false for no_op>,
    "directive_type": "solar_reduction" | "minimum_battery_reserve" | "no_charge_window" | "no_discharge_window" | "max_grid_window" | "no_op",
    "structured_adjustment": {
      "hours": [<unique integers 0-23, ascending>],
      "factor": <solar_reduction only: usable fraction remaining, 0..1>,
      "minimum_energy_kwh": <minimum_battery_reserve only: required energy after the hour, 0..capacity>,
      "max_grid_kwh": <max_grid_window only: maximum grid import per hour, >= 0>
    },
    "explanation": "<short reason>"
  }
]}

Rules:
- Exactly one entry per note; note_index ascending, starting at 0.
- "structured_adjustment" is null for no_op and includes only the fields relevant to the directive type.
- Hours use a 24-hour clock and are start-inclusive, end-exclusive. "6 PM until 9 PM" -> [18, 19, 20]. "1 PM to 3 PM" -> [13, 14]. "midnight to 2 AM" -> [0, 1].
- solar_reduction "factor" is the fraction REMAINING, never the reduction amount: "80% reduction" -> 0.2; "25% of forecast" -> 0.25; "half the forecast" -> 0.5; "cut solar by a third" -> 0.6667.
- minimum_battery_reserve "minimum_energy_kwh" is the battery energy required after each listed hour.
- max_grid_window "max_grid_kwh" is the maximum grid import allowed in each listed hour.
- no_charge_window and no_discharge_window list only "hours".
- no_op: applies=false, structured_adjustment=null, for notes unrelated to today's schedule (distractors).

Examples:
Note: "Reduce solar output by 80% from 11 AM to 1 PM."
Output: {"directives": [{"note_index": 0, "applies": true, "directive_type": "solar_reduction", "structured_adjustment": {"hours": [11, 12], "factor": 0.2}, "explanation": "80% reduction leaves 20% usable solar in hours 11-12."}]}

Note: "Use only half the solar forecast between noon and 3 PM."
Output: {"directives": [{"note_index": 0, "applies": true, "directive_type": "solar_reduction", "structured_adjustment": {"hours": [12, 13, 14], "factor": 0.5}, "explanation": "Half the forecast means a usable factor of 0.5."}]}

Note: "Keep 25% of the solar between 9 AM and 11 AM."
Output: {"directives": [{"note_index": 0, "applies": true, "directive_type": "solar_reduction", "structured_adjustment": {"hours": [9, 10], "factor": 0.25}, "explanation": "Only a quarter of the solar forecast remains usable."}]}

Note: "The data center needs at least 80 kWh in the battery from 6 PM until 10 PM."
Output: {"directives": [{"note_index": 0, "applies": true, "directive_type": "minimum_battery_reserve", "structured_adjustment": {"hours": [18, 19, 20, 21], "minimum_energy_kwh": 80}, "explanation": "Backup reserve of 80 kWh required during the evening window."}]}

Note: "Do not charge the battery from 2 AM to 4 AM."
Output: {"directives": [{"note_index": 0, "applies": true, "directive_type": "no_charge_window", "structured_adjustment": {"hours": [2, 3]}, "explanation": "Charging is unavailable in hours 2-3."}]}

Note: "No discharging between 7 PM and 9 PM."
Output: {"directives": [{"note_index": 0, "applies": true, "directive_type": "no_discharge_window", "structured_adjustment": {"hours": [19, 20]}, "explanation": "Discharging is unavailable in hours 19-20."}]}

Note: "Grid intake must stay at or below 190 kWh from 7 PM until 10 PM while the substation is constrained."
Output: {"directives": [{"note_index": 0, "applies": true, "directive_type": "max_grid_window", "structured_adjustment": {"hours": [19, 20, 21], "max_grid_kwh": 190}, "explanation": "Grid import is capped at 190 kWh during the constraint."}]}

Note: "A seminar room booking was moved to next week."
Output: {"directives": [{"note_index": 0, "applies": false, "directive_type": "no_op", "structured_adjustment": null, "explanation": "This note does not affect today's energy schedule."}]}

Combined notes -> one object per note, same order:
Notes: ["Cap grid import at 100 kWh from 5 PM to 7 PM.", "Battery maintenance is scheduled for next month."]
Output: {"directives": [{"note_index": 0, "applies": true, "directive_type": "max_grid_window", "structured_adjustment": {"hours": [17, 18], "max_grid_kwh": 100}, "explanation": "Grid import capped at 100 kWh in hours 17-18."}, {"note_index": 1, "applies": false, "directive_type": "no_op", "structured_adjustment": null, "explanation": "This note does not affect today's energy schedule."}]}
"""


def build_messages(notes: list[str], battery: BatterySpec) -> list[dict[str, str]]:
    """Build the Chat Completions message list for the given notes."""

    lines = [
        f"Battery capacity: {battery.capacity_kwh:g} kWh.",
        "Interpret each operator note below, preserving order:",
    ]
    lines.extend(f"{index}: {note}" for index, note in enumerate(notes))
    lines.append(
        'Return ONLY {"directives": [...]} with exactly one object per note.'
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]
