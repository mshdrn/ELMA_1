import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import median


CHECK = "Проверка заявки"
MANAGER = "Согласование руководителем"
BUDGET = "Согласование бюджета"
LEGAL = "Проверка договора"
FULFILL = "Исполнение заявки"
REWORK = "Доработка заявки"
AUDIT = "Внеплановая проверка"
CUTOFF = datetime.fromisoformat("2026-07-01T00:00:00+03:00")
ROLES = {
    CHECK: "specialist", MANAGER: "manager", BUDGET: "finance",
    LEGAL: "legal", FULFILL: "procurement", REWORK: "requester",
}


def working_hours(start, end):
    hours = 0
    day = start.date()
    while day <= end.date():
        if day.weekday() < 5:
            morning = datetime.combine(day, time(9), start.tzinfo)
            evening = datetime.combine(day, time(18), start.tzinfo)
            hours += max(0, (min(end, evening) - max(start, morning)).total_seconds()) / 3600
        day += timedelta(days=1)
    return hours


source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("students.csv")
folder = Path(__file__).parent
cases = defaultdict(list)
event_ids = set()
future_events = []
with source.open(encoding="utf-8-sig", newline="") as file:
    reader = csv.DictReader(file)
    fields = reader.fieldnames
    for event in reader:
        assert event["EventId"] not in event_ids
        event_ids.add(event["EventId"])
        moment = datetime.fromisoformat(event["Timestamp"])
        if moment > CUTOFF:
            future_events.append(event)
            continue
        event["EventIndex"] = int(event["EventIndex"])
        event["Timestamp"] = moment
        cases[event["ProcessInstanceId"]].append(event)

with (folder / "events_after_cutoff.csv").open("w", encoding="utf-8", newline="") as file:
    writer = csv.DictWriter(file, fieldnames=fields)
    writer.writeheader()
    writer.writerows(future_events)

typical_times = defaultdict(list)
created_tasks = {
    event["TaskId"]: event for events in cases.values() for event in events
    if event["EventType"] == "task_created"
}
task_durations = {}
for events in cases.values():
    for event in events:
        if event["EventType"] == "task_completed":
            start = created_tasks[event["TaskId"]]["Timestamp"]
            end = event["Timestamp"]
            elapsed = (end - start).total_seconds()
            assert elapsed >= 0
            work = working_hours(start, end)
            task_durations[event["TaskId"]] = (work, elapsed)
            typical_times[event["RequestType"], event["ActivityName"]].append((work, elapsed / 3600))

median_hours = {key: median(work for work, elapsed in values) for key, values in typical_times.items()}
states = Counter()
routes = defaultdict(Counter)
results = []
for case_id, events in sorted(cases.items()):
    events.sort(key=lambda event: event["EventIndex"])
    assert [event["EventIndex"] for event in events] == list(range(1, len(events) + 1))
    assert all(first["Timestamp"] <= second["Timestamp"] for first, second in zip(events, events[1:]))
    request_type = events[0]["RequestType"]
    outcome = events[-1]["Result"] if events[-1]["EventType"] == "process_completed" else "open"
    states[request_type, outcome] += 1
    created = {event["TaskId"]: event for event in events if event["EventType"] == "task_created"}
    closed = [event for event in events if event["EventType"] == "task_completed"]
    closed_ids = {event["TaskId"] for event in closed}
    names = [event["ActivityName"] for event in closed]
    routes[request_type][tuple((event["ActivityName"], event["Result"]) for event in closed)] += 1
    findings = []

    if outcome == "fulfilled":
        expected = [CHECK, MANAGER, BUDGET]
        if request_type == "contract":
            expected.append(LEGAL)
        expected.append(FULFILL)
        final_names = [
            event["ActivityName"] for event in closed
            if event["Result"] == "accept" and event["ActivityName"] in expected
        ]
        for name in expected:
            if name in final_names:
                continue
            findings.append(("Пропуск обязательного этапа",
                f"Заявка исполнена, но пропущен этап «{name}». Маршрут: {' → '.join(names)}."))
        for task in created.values():
            if task["ActivityName"] != FULFILL:
                continue
            for name in expected[:-1]:
                approvals = [event for event in closed if event["ActivityName"] == name and event["Result"] == "accept"]
                if approvals and all(event["Timestamp"] > task["Timestamp"] for event in approvals):
                    end = approvals[-1]["Timestamp"]
                    findings.append(("Нарушен порядок этапов",
                        f"Исполнение создано {task['Timestamp'].isoformat()}, событие {task['EventIndex']}. "
                        f"Этап «{name}» закрыт позже, {end.isoformat()}. Этот этап должен пройти до исполнения."))

    wrong_roles = []
    extra_checks = []
    long_tasks = []
    seconds = []
    for position, event in enumerate(closed):
        name = event["ActivityName"]
        index = event["EventIndex"]
        if name in ROLES and event["Role"] != ROLES[name]:
            same_user = " Действие выполнил автор заявки." if event["UserId"] == events[0]["UserId"] else ""
            wrong_roles.append(
                f"«{name}», событие {index}: роль {event['Role']} вместо {ROLES[name]}.{same_user}"
            )
        if name == CHECK and position > 0 and names[position - 1] == CHECK:
            extra_checks.append(f"{closed[position - 1]['EventIndex']} и {index}")
        work, elapsed = task_durations[event["TaskId"]]
        seconds.append(elapsed)
        normal = median_hours[request_type, name]
        threshold = 5 * normal
        if len(typical_times[request_type, name]) >= 20 and work > threshold:
            long_tasks.append(
                f"«{name}», событие {index}: {work:.2f} рабочих ч, {elapsed / 3600:.2f} календарных ч. "
                f"Медиана {normal:.2f} рабочих ч, порог {threshold:.2f} рабочих ч"
            )

    if wrong_roles:
        findings.append(("Несоответствие роли", " ".join(wrong_roles)))
    if extra_checks:
        findings.append(("Повтор проверки без возврата",
            f"Две проверки подряд без возврата и доработки. Номера событий: {', '.join(extra_checks)}."))
    if long_tasks:
        findings.append(("Долгое закрытое задание",
            f"{' / '.join(long_tasks)}. Тип заявки: {request_type}."))

    returns = sum(event["Result"] == "return" for event in closed)
    if returns >= 3:
        findings.append(("Много возвратов", f"Руководитель вернул заявку {returns} раз. Типичный случай с возвратом имеет одну доработку."))
    if outcome != "open" and len(seconds) == len(closed) and len(seconds) >= 2 and max(seconds) <= 60:
        durations = ", ".join(str(int(value)) for value in seconds)
        findings.append(("Очень быстрые задания",
            f"Все {len(seconds)} задания закрыты за одну минуту или быстрее. Длительности по порядку в секундах: {durations}."))
    audits = [str(event["EventIndex"]) for event in closed if event["ActivityName"] == AUDIT]
    if audits:
        findings.append(("Внеплановая проверка — уточнить причину",
            f"Добавлено внеплановых проверок: {len(audits)}. Номера событий: {', '.join(audits)}. Причину нужно проверить вручную."))
    if outcome == "open":
        for task_id, event in created.items():
            if task_id not in closed_ids:
                name = event["ActivityName"]
                if len(typical_times.get((request_type, name), [])) < 20:
                    continue
                wait = working_hours(event["Timestamp"], CUTOFF)
                normal = median_hours[request_type, name]
                threshold = 5 * normal
                if wait > threshold:
                    age = (CUTOFF - event["Timestamp"]).total_seconds() / 3600
                    findings.append(("Долгое открытое задание",
                        f"«{name}», событие {event['EventIndex']}. На дату среза открыто уже {wait:.1f} рабочих ч "
                        f"({age:.1f} календарных ч). Медиана {normal:.2f} рабочих ч, порог {threshold:.2f} рабочих ч."))

    for kind, explanation in findings:
        results.append((case_id, kind, explanation))

destination = folder / "anomalies.csv"
with destination.open("w", encoding="utf-8", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["ProcessInstanceId", "AnomalyType", "Explanation"])
    writer.writerows(results)

print("Заявок:", len(cases))
print("Всего событий в файле:", len(event_ids))
print("Событий после среза:", len(future_events))
print("Событий на дату среза:", sum(map(len, cases.values())))
print("Исходы по типам:", sorted(states.items()))
for request_type, counts in sorted(routes.items()):
    route, count = counts.most_common(1)[0]
    print(request_type, count, " → ".join(name for name, result in route))
for (request_type, name), values in sorted(typical_times.items()):
    print(f"{request_type}, {name}: {median(value[0] for value in values):.2f} рабочих ч, "
          f"{median(value[1] for value in values):.2f} календарных ч")
print("Найдено заявок:", len({case_id for case_id, kind, explanation in results}))
for kind, count in Counter(kind for case_id, kind, explanation in results).most_common():
    print(f"{kind}: {count}")
