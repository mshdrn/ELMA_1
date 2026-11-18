import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median


ORDER_FIELDS = "id user_id product_id amount status created_at updated_at pre_payment trial period_id provider_id"
PAYMENT_FIELDS = "id order_id amount payment_link status raw_response_payment created_at updated_at"
SUBSCRIPTION_FIELDS = "id user_id product_id expired_time created_at order_id disabled_at next_product_id pre_payment pre_payment_notification next_period_id"
DEVICE_FIELDS = "id user_id device_identifier device_name client_type os last_seen in_sub brand fcm_push_token hms_push_token push_tokens_updated_at"
DEVICE_CODE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
ZERO_CODE = "00000000-0000-0000-0000-000000000000"


def read_rows(folder, name, fields):
    with (folder / name).open(encoding="utf-8-sig", newline="") as file:
        yield from csv.DictReader(file, delimiter=";", fieldnames=fields.split())


def timestamp(value):
    base, point, fraction = value.partition(".")
    if point:
        value = base + "." + fraction.ljust(6, "0")
    return datetime.fromisoformat(value)


def save_first(dates, key, value):
    dates[key] = min(dates.get(key, value), value)


folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
completed_payments = {}
for row in read_rows(folder, "transactions.csv", PAYMENT_FIELDS):
    if row["status"] == "completed" and float(row["amount"]) > 0:
        save_first(completed_payments, row["order_id"], timestamp(row["updated_at"]))

first_purchase = {}
trial_orders = {}
for row in read_rows(folder, "orders.csv", ORDER_FIELDS):
    order_id = row["id"]
    if row["status"] != "paid" or order_id not in completed_payments:
        continue
    if row["trial"] == "t":
        trial_orders[order_id] = row["user_id"]
    elif row["trial"] == "f":
        save_first(first_purchase, row["user_id"], completed_payments[order_id])

first_trial = {}
for row in read_rows(folder, "subscribe.csv", SUBSCRIPTION_FIELDS):
    user_id = row["user_id"]
    if trial_orders.get(row["order_id"]) == user_id:
        save_first(first_trial, user_id, timestamp(row["created_at"]))

days_to_purchase = []
trial_after_purchase = 0
for user_id, purchase in first_purchase.items():
    if user_id not in first_trial:
        continue
    days = (purchase - first_trial[user_id]).total_seconds() / 86400
    if days >= 0:
        days_to_purchase.append(days)
    else:
        trial_after_purchase += 1

buyers = len(first_purchase)
after_trial = len(days_to_purchase)
no_trial = buyers - after_trial - trial_after_purchase
print(f"Покупателей: {buyers}. Аккаунтов с триалом: {len(first_trial)}.")
print(f"Первая покупка после триала: {after_trial} ({after_trial / buyers:.1%}).")
print(f"Первая покупка до триала: {trial_after_purchase}. Покупка без записи о триале: {no_trial}.")
print(f"Медиана от начала триала до покупки: {median(days_to_purchase):.4f} суток.")
print(f"Покупок между 3 и 4 сутками: {sum(3 < gap <= 4 for gap in days_to_purchase)}.")

device_users = defaultdict(set)
for row in read_rows(folder, "devices.csv", DEVICE_FIELDS):
    code = row["device_identifier"].strip().lower()
    user_id = row["user_id"]
    if row["client_type"] == "mobile" and user_id and code != ZERO_CODE and DEVICE_CODE.fullmatch(code):
        device_users[code].add(user_id)

two_users = 0
two_trials = 0
matching_codes = 0
matching_pairs = set()
for users in device_users.values():
    if len(users) != 2:
        continue
    two_users += 1
    if not users <= first_trial.keys():
        continue
    two_trials += 1
    start, end = sorted(first_trial[user_id] for user_id in users)
    days = (end - start).total_seconds() / 86400
    if 3 <= days <= 7:
        matching_codes += 1
        matching_pairs.add(tuple(sorted(users)))

print(f"Мобильных кодов в формате UUID: {len(device_users)}.")
print(f"Кодов ровно с двумя аккаунтами: {two_users}. Триал у обоих: {two_trials}.")
print(f"Второй триал через 3–7 суток: {matching_codes} кодов, {len(matching_pairs)} разных пар аккаунтов.")
