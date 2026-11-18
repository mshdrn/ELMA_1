import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path


source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("students.csv")
folder = Path(__file__).parent
cases = defaultdict(list)
with source.open(encoding="utf-8-sig", newline="") as file:
    for event in csv.DictReader(file):
        event["EventIndex"] = int(event["EventIndex"])
        cases[event["ProcessInstanceId"]].append(event)

variants = Counter()
transitions = Counter()
examples = {}
for case_id, events in cases.items():
    events.sort(key=lambda event: event["EventIndex"])
    route = tuple(event["ActivityName"] for event in events)
    variants[route] += 1
    transitions.update(zip(route, route[1:]))
    examples.setdefault(route, case_id)

with (folder / "variants.csv").open("w", encoding="utf-8", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["Variant", "Route", "NumberOfCases", "Share"])
    for number, (route, count) in enumerate(variants.most_common(), start=1):
        writer.writerow([number, " > ".join(route), count, f"{count / len(cases):.3f}"])
        print(f"Вариант {number}: {count} заявок. Пример: {examples[route]}")

with (folder / "student_transitions.csv").open("w", encoding="utf-8", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["SourceActivity", "TargetActivity"])
    writer.writerows(sorted(transitions))

print(f"Заявок: {len(cases)}. Событий: {sum(map(len, cases.values()))}.")
for (start, end), count in transitions.items():
    print(f"{start} → {end}: {count}")
