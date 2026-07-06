from __future__ import annotations

import argparse
import itertools
import json
import random
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[3]
GEN_DIR = ROOT / "AutomateQuery" / "NLP" / "training_generator"
RASA_DATA_DIR = ROOT / "AutomateQuery" / "NLP" / "rasa_nlu" / "data"

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)(?::([a-zA-Z0-9_]+))?\}")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def find_placeholders(template: str) -> List[Tuple[str, str | None]]:
    return PLACEHOLDER_RE.findall(template)


def render_template(template: str, values: Dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        slot = match.group(1)
        entity = match.group(2)
        value = values[slot]

        if entity:
            return f"[{value}]({entity})"

        return value

    return PLACEHOLDER_RE.sub(repl, template)


def generate_examples_for_intent(
    templates: List[str],
    samples: Dict[str, List[str]],
    *,
    max_examples: int,
    seed: int,
) -> List[str]:
    rng = random.Random(seed)
    examples: List[str] = []
    seen = set()

    template_order = list(templates)
    rng.shuffle(template_order)

    for template in template_order:
        placeholders = find_placeholders(template)

        if not placeholders:
            candidate = template.strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                examples.append(candidate)
            continue

        slot_names: List[str] = []
        for slot, _entity in placeholders:
            if slot not in slot_names:
                slot_names.append(slot)

        value_lists: List[List[str]] = []
        for slot in slot_names:
            if slot not in samples:
                raise KeyError(f"Missing sample values for placeholder '{slot}' in template: {template}")

            vals = list(samples[slot])
            rng.shuffle(vals)
            value_lists.append(vals)

        combos = list(itertools.product(*value_lists))
        rng.shuffle(combos)

        for combo in combos:
            values = dict(zip(slot_names, combo))
            candidate = render_template(template, values).strip()

            if candidate and candidate not in seen:
                seen.add(candidate)
                examples.append(candidate)

            if len(examples) >= max_examples:
                return examples

    return examples[:max_examples]


def write_rasa_yml(intent_examples: Dict[str, List[str]], output_path: Path) -> None:
    lines: List[str] = []
    lines.append('version: "3.1"')
    lines.append("")
    lines.append("nlu:")

    for intent, examples in intent_examples.items():
        lines.append(f"  - intent: {intent}")
        lines.append("    examples: |")
        for ex in examples:
            lines.append(f"      - {ex}")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--templates", type=Path, default=GEN_DIR / "intent_templates.json")
    parser.add_argument("--samples", type=Path, default=GEN_DIR / "sample_entities.json")
    parser.add_argument("--output", type=Path, default=RASA_DATA_DIR / "nlu_generated.yml")
    parser.add_argument("--max-per-intent", type=int, default=80)
    parser.add_argument("--seed", type=int, default=27)
    args = parser.parse_args()

    templates: Dict[str, List[str]] = load_json(args.templates)
    samples: Dict[str, List[str]] = load_json(args.samples)

    all_examples: Dict[str, List[str]] = {}

    for index, (intent, intent_templates) in enumerate(templates.items()):
        examples = generate_examples_for_intent(
            intent_templates,
            samples,
            max_examples=args.max_per_intent,
            seed=args.seed + index,
        )
        all_examples[intent] = examples

    write_rasa_yml(all_examples, args.output)

    total = sum(len(v) for v in all_examples.values())

    print("=" * 100)
    print("RASA NLU TRAINING DATA GENERATED")
    print("=" * 100)
    print("OUTPUT:", args.output)
    print("INTENTS:", len(all_examples))
    print("TOTAL_EXAMPLES:", total)
    print("MAX_PER_INTENT:", args.max_per_intent)
    print()
    for intent, examples in all_examples.items():
        print(f"{intent}: {len(examples)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
