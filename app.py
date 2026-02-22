#!/usr/bin/env python3
"""家計簿アプリ - Flask APIサーバー（複式簿記 + キャッシュフロー + 長期資産/負債対応版）"""

import json
import os
import calendar as cal
from datetime import date, timedelta
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")

DEFAULT_DATA = {
    "accounts": [
        {"id": 1, "name": "現金", "type": "asset", "class": "current", "balance": 0},
        {"id": 2, "name": "普通預金", "type": "asset", "class": "current", "balance": 0},
        {"id": 3, "name": "PayPay", "type": "asset", "class": "current", "balance": 0},
        {"id": 4, "name": "Vpoint", "type": "asset", "class": "current", "balance": 0},
        {"id": 5, "name": "クレジットカード", "type": "liability", "class": "current", "balance": 0,
         "payDay": 27, "payFromAccountId": 2},
        {"id": 6, "name": "支払い予定", "type": "liability", "class": "current", "balance": 0},
    ],
    "transactions": [],
    "fixedCosts": [],
    "incomeSchedule": [],
    "categories": {
        "expense": [
            "食費", "交通費", "交際費", "日用品費", "趣味・娯楽費",
            "通信費", "水道光熱費", "住居費", "保険料", "医療費",
            "被服費", "教育費", "雑費",
        ],
        "income": ["給与", "バイト代", "賞与", "副業", "雑収入"],
    },
    "tags": [],
}


def load_data():
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA.copy()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return migrate_data(data)


def migrate_data(data):
    changed = False
    if "accounts" not in data:
        old_balance = data.pop("balance", 0)
        data["accounts"] = [
            {"id": 1, "name": "現金", "type": "asset", "class": "current", "balance": old_balance},
            {"id": 2, "name": "普通預金", "type": "asset", "class": "current", "balance": 0},
        ]
        for tx in data.get("transactions", []):
            if "accountId" not in tx:
                tx["accountId"] = 1
        for fc in data.get("fixedCosts", []):
            if "accountId" not in fc:
                fc["accountId"] = 2
        for inc in data.get("incomeSchedule", []):
            if "accountId" not in inc:
                inc["accountId"] = 2
        changed = True
    if "categories" not in data:
        data["categories"] = DEFAULT_DATA["categories"].copy()
        changed = True
    if "tags" not in data:
        data["tags"] = []
        changed = True
    # class フィールド追加
    for acc in data.get("accounts", []):
        if "class" not in acc:
            acc["class"] = "current"
            changed = True
    # 固定費にタグを追加
    for fc in data.get("fixedCosts", []):
        if "tags" not in fc:
            fc["tags"] = []
            changed = True
    # 支払い予定口座がなければ追加
    names = [a["name"] for a in data.get("accounts", [])]
    if "支払い予定" not in names:
        data["accounts"].append({
            "id": next_id(data["accounts"]),
            "name": "支払い予定",
            "type": "liability",
            "class": "current",
            "balance": 0,
        })
        changed = True
    if changed:
        save_data(data)
    return data


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def next_id(items):
    if not items:
        return 1
    return max(item["id"] for item in items) + 1


def get_account(data, account_id):
    return next((a for a in data["accounts"] if a["id"] == account_id), None)


def calc_pending_income(data):
    """未到着の収入を計算（未来日付のincome取引で流動資産口座に記録されたもの）
    帳簿残高に含まれてるが実際にはまだ届いてないお金"""
    today_iso = date.today().isoformat()
    total = 0
    tx_ids = set()
    for tx in data["transactions"]:
        if tx["type"] == "income" and tx["date"] > today_iso:
            acc = get_account(data, tx.get("accountId"))
            if acc and acc.get("class") == "current":
                total += tx["amount"]
                tx_ids.add(tx["id"])
    return total, tx_ids


# ─── Pages ───

@app.route("/")
def index():
    return render_template("index.html")


# ─── Accounts ───

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    data = load_data()
    result = []
    for a in data["accounts"]:
        acc = dict(a)
        acc["todayBalance"] = a["balance"]
        result.append(acc)
    return jsonify(result)


@app.route("/api/accounts", methods=["POST"])
def add_account():
    data = load_data()
    body = request.get_json()
    acc = {
        "id": next_id(data["accounts"]),
        "name": body["name"],
        "type": body.get("type", "asset"),
        "class": body.get("class", "current"),
        "balance": int(body.get("balance", 0)),
    }
    if acc["type"] == "liability" and acc["class"] == "current":
        acc["payDay"] = int(body.get("payDay", 0))
        acc["payFromAccountId"] = int(body.get("payFromAccountId", 0))
    data["accounts"].append(acc)
    save_data(data)
    return jsonify(acc), 201


@app.route("/api/accounts/<int:acc_id>", methods=["PUT"])
def update_account(acc_id):
    data = load_data()
    acc = get_account(data, acc_id)
    if not acc:
        return jsonify({"error": "not found"}), 404
    body = request.get_json()
    acc["name"] = body.get("name", acc["name"])
    acc["type"] = body.get("type", acc["type"])
    acc["class"] = body.get("class", acc.get("class", "current"))
    if "balance" in body:
        new_balance = int(body["balance"])
        old_balance = acc["balance"]
        diff = new_balance - old_balance
        if diff != 0:
            # 残高調整仕訳を自動作成（カレンダー・帳簿の整合性維持）
            if acc["type"] == "asset":
                tx_type = "income" if diff > 0 else "expense"
            else:
                tx_type = "expense" if diff > 0 else "income"
            adj_tx = {
                "id": next_id(data["transactions"]),
                "date": date.today().isoformat(),
                "amount": abs(diff),
                "type": tx_type,
                "category": "雑収入" if tx_type == "income" else "雑費",
                "tags": [],
                "schedule": "",
                "memo": f"残高調整: {acc['name']}: {old_balance} → {new_balance}",
                "accountId": acc_id,
            }
            # 残高は仕訳で自動調整されるので直接セット
            acc["balance"] = new_balance
            data["transactions"].append(adj_tx)
    if acc["type"] == "liability" and acc.get("class") == "current":
        if "payDay" in body:
            acc["payDay"] = int(body["payDay"])
        if "payFromAccountId" in body:
            acc["payFromAccountId"] = int(body["payFromAccountId"])
    save_data(data)
    return jsonify(acc)


@app.route("/api/accounts/<int:acc_id>", methods=["DELETE"])
def delete_account(acc_id):
    data = load_data()
    has_tx = any(
        t.get("accountId") == acc_id
        or t.get("fromAccountId") == acc_id
        or t.get("toAccountId") == acc_id
        for t in data["transactions"]
    )
    if has_tx:
        return jsonify({"error": "この口座は取引で使用されているため削除できません"}), 400
    data["accounts"] = [a for a in data["accounts"] if a["id"] != acc_id]
    save_data(data)
    return jsonify({"ok": True})


# ─── Transactions ───

@app.route("/api/transactions", methods=["GET"])
def get_transactions():
    data = load_data()
    return jsonify(data["transactions"])


@app.route("/api/transactions", methods=["POST"])
def add_transaction():
    data = load_data()
    body = request.get_json()
    tx_type = body["type"]

    tx = {
        "id": next_id(data["transactions"]),
        "date": body["date"],
        "amount": int(body["amount"]),
        "type": tx_type,
        "category": body.get("category", ""),
        "tags": body.get("tags", []),
        "schedule": body.get("schedule", ""),
        "memo": body.get("memo", ""),
    }

    if tx_type == "transfer":
        tx["fromAccountId"] = int(body["fromAccountId"])
        tx["toAccountId"] = int(body["toAccountId"])
        tx["category"] = "振替"

        from_acc = get_account(data, tx["fromAccountId"])
        to_acc = get_account(data, tx["toAccountId"])
        if not from_acc or not to_acc:
            return jsonify({"error": "口座が見つかりません"}), 400

        from_acc["balance"] -= tx["amount"]
        if to_acc["type"] == "liability":
            to_acc["balance"] -= tx["amount"]
        else:
            to_acc["balance"] += tx["amount"]
    elif tx_type == "cc_detail":
        # CC明細: P/L・分析用に記録するが残高は変えない
        tx["accountId"] = int(body["accountId"])
        acc = get_account(data, tx["accountId"])
        if not acc:
            return jsonify({"error": "口座が見つかりません"}), 400
        # 残高操作なし
    else:
        tx["accountId"] = int(body["accountId"])
        acc = get_account(data, tx["accountId"])
        if not acc:
            return jsonify({"error": "口座が見つかりません"}), 400

        if tx_type == "expense":
            if acc["type"] == "liability":
                acc["balance"] += tx["amount"]
            else:
                acc["balance"] -= tx["amount"]
        elif tx_type == "income":
            acc["balance"] += tx["amount"]

    for tag in tx["tags"]:
        if tag and tag not in data["tags"]:
            data["tags"].append(tag)

    data["transactions"].append(tx)
    save_data(data)
    return jsonify(tx), 201


@app.route("/api/transactions/<int:tx_id>", methods=["PUT"])
def update_transaction(tx_id):
    data = load_data()
    tx = next((t for t in data["transactions"] if t["id"] == tx_id), None)
    if not tx:
        return jsonify({"error": "not found"}), 404
    body = request.get_json()

    # 1. 旧仕訳の残高を逆仕訳で戻す
    if tx["type"] == "cc_detail":
        pass
    elif tx["type"] == "transfer":
        from_acc = get_account(data, tx.get("fromAccountId"))
        to_acc = get_account(data, tx.get("toAccountId"))
        if from_acc:
            from_acc["balance"] += tx["amount"]
        if to_acc:
            if to_acc["type"] == "liability":
                to_acc["balance"] += tx["amount"]
            else:
                to_acc["balance"] -= tx["amount"]
    else:
        acc = get_account(data, tx.get("accountId"))
        if acc:
            if tx["type"] == "expense":
                if acc["type"] == "liability":
                    acc["balance"] -= tx["amount"]
                else:
                    acc["balance"] += tx["amount"]
            elif tx["type"] == "income":
                acc["balance"] -= tx["amount"]

    # 2. フィールド更新
    tx["date"] = body.get("date", tx["date"])
    tx["amount"] = int(body.get("amount", tx["amount"]))
    tx["category"] = body.get("category", tx["category"])
    tx["tags"] = body.get("tags", tx.get("tags", []))
    tx["schedule"] = body.get("schedule", tx.get("schedule", ""))
    tx["memo"] = body.get("memo", tx["memo"])
    if "accountId" in body:
        tx["accountId"] = int(body["accountId"])

    # 3. 新しい残高を適用
    if tx["type"] == "cc_detail":
        pass
    elif tx["type"] == "transfer":
        from_acc = get_account(data, tx.get("fromAccountId"))
        to_acc = get_account(data, tx.get("toAccountId"))
        if from_acc:
            from_acc["balance"] -= tx["amount"]
        if to_acc:
            if to_acc["type"] == "liability":
                to_acc["balance"] -= tx["amount"]
            else:
                to_acc["balance"] += tx["amount"]
    else:
        acc = get_account(data, tx.get("accountId"))
        if acc:
            if tx["type"] == "expense":
                if acc["type"] == "liability":
                    acc["balance"] += tx["amount"]
                else:
                    acc["balance"] -= tx["amount"]
            elif tx["type"] == "income":
                acc["balance"] += tx["amount"]

    for tag in tx.get("tags", []):
        if tag and tag not in data["tags"]:
            data["tags"].append(tag)

    save_data(data)
    return jsonify(tx)


@app.route("/api/transactions/<int:tx_id>", methods=["DELETE"])
def delete_transaction(tx_id):
    data = load_data()
    tx = next((t for t in data["transactions"] if t["id"] == tx_id), None)
    if not tx:
        return jsonify({"error": "not found"}), 404

    if tx["type"] == "cc_detail":
        pass  # 残高操作なし
    elif tx["type"] == "transfer":
        from_acc = get_account(data, tx.get("fromAccountId"))
        to_acc = get_account(data, tx.get("toAccountId"))
        if from_acc:
            from_acc["balance"] += tx["amount"]
        if to_acc:
            if to_acc["type"] == "liability":
                to_acc["balance"] += tx["amount"]
            else:
                to_acc["balance"] -= tx["amount"]
    else:
        acc = get_account(data, tx.get("accountId"))
        if acc:
            if tx["type"] == "expense":
                if acc["type"] == "liability":
                    acc["balance"] -= tx["amount"]
                else:
                    acc["balance"] += tx["amount"]
            elif tx["type"] == "income":
                acc["balance"] -= tx["amount"]

    data["transactions"] = [t for t in data["transactions"] if t["id"] != tx_id]
    save_data(data)
    return jsonify({"ok": True})


# ─── Fixed Costs ───

@app.route("/api/fixed-costs", methods=["GET"])
def get_fixed_costs():
    data = load_data()
    return jsonify(data["fixedCosts"])


@app.route("/api/fixed-costs", methods=["POST"])
def add_fixed_cost():
    data = load_data()
    body = request.get_json()
    fc = {
        "id": next_id(data["fixedCosts"]),
        "name": body["name"],
        "amount": int(body["amount"]),
        "category": body.get("category", "雑費"),
        "day": int(body["day"]),
        "accountId": int(body.get("accountId", data["accounts"][0]["id"])),
        "tags": body.get("tags", []),
    }
    for tag in fc["tags"]:
        if tag and tag not in data["tags"]:
            data["tags"].append(tag)
    data["fixedCosts"].append(fc)
    save_data(data)
    return jsonify(fc), 201


@app.route("/api/fixed-costs/<int:fc_id>", methods=["PUT"])
def update_fixed_cost(fc_id):
    data = load_data()
    fc = next((f for f in data["fixedCosts"] if f["id"] == fc_id), None)
    if not fc:
        return jsonify({"error": "not found"}), 404
    body = request.get_json()
    fc["name"] = body.get("name", fc["name"])
    fc["amount"] = int(body.get("amount", fc["amount"]))
    fc["category"] = body.get("category", fc["category"])
    fc["day"] = int(body.get("day", fc["day"]))
    fc["accountId"] = int(body.get("accountId", fc.get("accountId", 1)))
    fc["tags"] = body.get("tags", fc.get("tags", []))
    for tag in fc["tags"]:
        if tag and tag not in data["tags"]:
            data["tags"].append(tag)
    save_data(data)
    return jsonify(fc)


@app.route("/api/fixed-costs/<int:fc_id>", methods=["DELETE"])
def delete_fixed_cost(fc_id):
    data = load_data()
    data["fixedCosts"] = [f for f in data["fixedCosts"] if f["id"] != fc_id]
    save_data(data)
    return jsonify({"ok": True})


# ─── Income Schedule ───

@app.route("/api/income-schedule", methods=["GET"])
def get_income_schedule():
    data = load_data()
    return jsonify(data["incomeSchedule"])


@app.route("/api/income-schedule", methods=["POST"])
def add_income_schedule():
    data = load_data()
    body = request.get_json()
    inc = {
        "id": next_id(data["incomeSchedule"]),
        "name": body["name"],
        "amount": int(body["amount"]),
        "day": int(body["day"]),
        "accountId": int(body.get("accountId", data["accounts"][0]["id"])),
    }
    data["incomeSchedule"].append(inc)
    save_data(data)
    return jsonify(inc), 201


@app.route("/api/income-schedule/<int:inc_id>", methods=["PUT"])
def update_income_schedule(inc_id):
    data = load_data()
    inc = next((i for i in data["incomeSchedule"] if i["id"] == inc_id), None)
    if not inc:
        return jsonify({"error": "not found"}), 404
    body = request.get_json()
    inc["name"] = body.get("name", inc["name"])
    inc["amount"] = int(body.get("amount", inc["amount"]))
    inc["day"] = int(body.get("day", inc["day"]))
    inc["accountId"] = int(body.get("accountId", inc.get("accountId", 1)))
    save_data(data)
    return jsonify(inc)


@app.route("/api/income-schedule/<int:inc_id>", methods=["DELETE"])
def delete_income_schedule(inc_id):
    data = load_data()
    data["incomeSchedule"] = [i for i in data["incomeSchedule"] if i["id"] != inc_id]
    save_data(data)
    return jsonify({"ok": True})


# ─── Categories ───

@app.route("/api/categories", methods=["GET"])
def get_categories():
    data = load_data()
    return jsonify(data["categories"])


@app.route("/api/categories", methods=["POST"])
def add_category():
    data = load_data()
    body = request.get_json()
    cat_type = body["type"]
    name = body["name"].strip()
    if not name:
        return jsonify({"error": "名前が空です"}), 400
    if name in data["categories"].get(cat_type, []):
        return jsonify({"error": "既に存在します"}), 400
    data["categories"].setdefault(cat_type, []).append(name)
    save_data(data)
    return jsonify({"ok": True}), 201


@app.route("/api/categories", methods=["DELETE"])
def delete_category():
    data = load_data()
    body = request.get_json()
    cat_type = body["type"]
    name = body["name"]
    cats = data["categories"].get(cat_type, [])
    if name in cats:
        cats.remove(name)
    save_data(data)
    return jsonify({"ok": True})


# ─── Tags ───

@app.route("/api/tags", methods=["GET"])
def get_tags():
    data = load_data()
    return jsonify(data["tags"])


@app.route("/api/tags", methods=["POST"])
def add_tag():
    data = load_data()
    body = request.get_json()
    tag = body["tag"].strip()
    if not tag:
        return jsonify({"error": "空です"}), 400
    if tag not in data["tags"]:
        data["tags"].append(tag)
        save_data(data)
    return jsonify({"ok": True}), 201


@app.route("/api/tags", methods=["DELETE"])
def delete_tag():
    data = load_data()
    body = request.get_json()
    tag = body["tag"]
    if tag in data["tags"]:
        data["tags"].remove(tag)
        save_data(data)
    return jsonify({"ok": True})


# ─── Summary ───

def is_cc_account(acc):
    """payDay を持つ流動負債か（クレジットカード的な口座）"""
    return (acc and acc["type"] == "liability"
            and acc.get("class") == "current" and acc.get("payDay", 0) > 0)


def get_cc_cycle_start(a, today):
    """CC口座の現在の請求サイクル開始日を返す（前回引落日）"""
    pay_day = a.get("payDay", 0)
    if not pay_day:
        return date(today.year, today.month, 1)
    if today.day >= pay_day:
        # 今月のpayDay以降 → サイクルは今月payDayから
        return date(today.year, today.month, pay_day)
    else:
        # 今月payDay未満 → サイクルは先月payDayから
        last_m = today.month - 1 if today.month > 1 else 12
        last_y = today.year if today.month > 1 else today.year - 1
        actual_day = min(pay_day, cal.monthrange(last_y, last_m)[1])
        return date(last_y, last_m, actual_day)


@app.route("/api/summary", methods=["GET"])
def get_summary():
    data = load_data()
    today = date.today()
    ym = f"{today.year}-{today.month:02d}"
    today_iso = today.isoformat()

    current_assets = sum(a["balance"] for a in data["accounts"]
                         if a["type"] == "asset" and a.get("class") == "current")
    current_liabilities = sum(a["balance"] for a in data["accounts"]
                              if a["type"] == "liability" and a.get("class") == "current")
    long_assets = sum(a["balance"] for a in data["accounts"]
                      if a["type"] == "asset" and a.get("class") == "long")
    long_liabilities = sum(a["balance"] for a in data["accounts"]
                           if a["type"] == "liability" and a.get("class") == "long")

    total_assets = current_assets + long_assets
    total_liabilities = current_liabilities + long_liabilities
    net_worth = total_assets - total_liabilities
    current_net = current_assets - current_liabilities

    # 今持ってるお金 = 流動資産 − 未到着収入
    pending_income, _ = calc_pending_income(data)
    hand = current_assets - pending_income

    # CC引落（payDayがある流動負債）とその他負債を分離
    cc_total = 0
    cc_list = []
    other_liabilities = 0
    for a in data["accounts"]:
        if a["type"] == "liability" and a.get("class") == "current":
            if is_cc_account(a) and a["balance"] > 0:
                cc_total += a["balance"]
                cc_list.append({
                    "name": a["name"], "balance": a["balance"],
                    "payDay": a["payDay"],
                })
            else:
                other_liabilities += max(0, a["balance"])

    # 今月の支出・収入
    month_expenses = sum(
        t["amount"] for t in data["transactions"]
        if t["type"] in ("expense", "cc_detail") and t["date"].startswith(ym)
    )
    month_income = sum(
        t["amount"] for t in data["transactions"]
        if t["type"] == "income" and t["date"].startswith(ym)
    )

    # 残りの固定費（アセット口座のみ、CC払い固定費は除外）
    remaining_fixed = 0
    for fc in data["fixedCosts"]:
        if fc["day"] > today.day:
            acc = get_account(data, fc.get("accountId"))
            if acc and acc["type"] == "asset":
                remaining_fixed += fc["amount"]
    remaining_income = sum(inc["amount"] for inc in data["incomeSchedule"]
                           if inc["day"] > today.day)

    # 今使える残高 = 持ってるお金 − CC引落 − その他負債
    usable_net = hand - cc_total - other_liabilities
    # あといくら使える = 今使える残高 − 固定費 + 定期収入
    spendable = usable_net - remaining_fixed + remaining_income

    return jsonify({
        "hand": hand,
        "usableNet": usable_net,
        "ccTotal": cc_total,
        "ccList": cc_list,
        "otherLiabilities": other_liabilities,
        "pendingIncome": pending_income,
        "currentAssets": current_assets,
        "currentLiabilities": current_liabilities,
        "longAssets": long_assets,
        "longLiabilities": long_liabilities,
        "totalAssets": total_assets,
        "totalLiabilities": total_liabilities,
        "netWorth": net_worth,
        "currentNet": current_net,
        "monthExpenses": month_expenses,
        "monthIncome": month_income,
        "remainingFixed": remaining_fixed,
        "remainingIncome": remaining_income,
        "spendable": spendable,
    })


# ─── Cash Flow ───

def build_cashflow_events(data, month_key, offset, today):
    """指定月のキャッシュフローイベント一覧を構築"""
    _, pending_ids = calc_pending_income(data)
    events = []

    # 未到着収入
    for tx in data["transactions"]:
        if tx["id"] not in pending_ids:
            continue
        if not tx["date"].startswith(month_key):
            continue
        day = int(tx["date"][8:10])
        if offset == 0 and day <= today.day:
            continue
        acc = get_account(data, tx.get("accountId"))
        sch = tx.get("schedule", "") or tx.get("category", "収入")
        events.append({
            "day": day, "name": sch, "amount": tx["amount"],
            "type": "income", "account": acc["name"] if acc else "",
            "cc": False, "liability_pay": False,
        })

    # 未来の負債口座取引（支払い予定など、CC以外）→ 実際のキャッシュアウト
    for tx in data["transactions"]:
        if tx["id"] in pending_ids:
            continue
        if not tx["date"].startswith(month_key):
            continue
        if tx["type"] not in ("expense", "income"):
            continue
        acc = get_account(data, tx.get("accountId"))
        if not acc or acc["type"] != "liability":
            continue
        if is_cc_account(acc):
            continue
        day = int(tx["date"][8:10])
        if offset == 0 and day <= today.day:
            continue
        sch = tx.get("schedule", "") or tx.get("category", "")
        events.append({
            "day": day, "name": sch,
            "amount": -tx["amount"] if tx["type"] == "expense" else tx["amount"],
            "type": tx["type"],
            "account": acc["name"] if acc else "",
            "cc": False, "liability_pay": True,
        })

    # CC明細実績（当月分 — 情報表示のみ、残高計算に影響しない cc=True）
    if offset == 0:
        for tx in data["transactions"]:
            if tx["type"] == "cc_detail" and tx["date"].startswith(month_key):
                tx_day = int(tx["date"][8:10])
                acc = get_account(data, tx.get("accountId"))
                cat = tx.get("category", "") or tx.get("memo", "CC明細")
                events.append({
                    "day": tx_day,
                    "name": cat,
                    "amount": -tx["amount"],
                    "type": "cc_detail",
                    "account": acc["name"] if acc else "",
                    "cc": True,   # 情報表示のみ。残高計算はCC雑費で行う
                    "liability_pay": False,
                })
        # CC未仕訳雑費（CC残高 - 当サイクルcc_detail - 固定費経過分）— 残高計算に影響する
        for a in data["accounts"]:
            if not is_cc_account(a):
                continue
            cycle_start = get_cc_cycle_start(a, today).isoformat()
            today_iso = today.isoformat()
            cc_det_sum = sum(t["amount"] for t in data["transactions"]
                             if t["type"] == "cc_detail" and t.get("accountId") == a["id"]
                             and cycle_start <= t["date"] <= today_iso)
            cc_fc_sum = sum(fc["amount"] for fc in data["fixedCosts"]
                            if fc.get("accountId") == a["id"] and fc["day"] <= today.day)
            unjournaled = a["balance"] - cc_det_sum - cc_fc_sum
            if unjournaled > 0:
                events.append({
                    "day": today.day,
                    "name": f"{a['name']} 雑費",
                    "amount": -unjournaled,
                    "type": "expense",
                    "account": a["name"],
                    "cc": False,   # 残高計算に影響する
                    "liability_pay": False,
                })

    # 固定費（CC固定費も含めてすべて残高計算に影響する cc=False）
    for fc in data["fixedCosts"]:
        day = fc["day"]
        if offset == 0 and day <= today.day:
            continue
        acc = get_account(data, fc.get("accountId"))
        events.append({
            "day": day, "name": fc["name"],
            "amount": -fc["amount"], "type": "expense",
            "account": acc["name"] if acc else "",
            "cc": False, "liability_pay": False,
        })

    # 定期収入
    for inc in data["incomeSchedule"]:
        day = inc["day"]
        if offset == 0 and day <= today.day:
            continue
        acc = get_account(data, inc.get("accountId"))
        events.append({
            "day": day, "name": inc["name"],
            "amount": inc["amount"], "type": "income",
            "account": acc["name"] if acc else "",
            "cc": False, "liability_pay": False,
        })

    events.sort(key=lambda e: e["day"])
    return events


@app.route("/api/cashflow", methods=["GET"])
def get_cashflow():
    data = load_data()
    today = date.today()
    ym = f"{today.year}-{today.month:02d}"

    current_assets = sum(a["balance"] for a in data["accounts"]
                         if a["type"] == "asset" and a.get("class") == "current")
    current_liabilities = sum(a["balance"] for a in data["accounts"]
                              if a["type"] == "liability" and a.get("class") == "current")

    pending_income, _ = calc_pending_income(data)
    hand = current_assets - pending_income

    # 今月の実績
    month_expenses = sum(
        t["amount"] for t in data["transactions"]
        if t["type"] in ("expense", "cc_detail") and t["date"].startswith(ym)
    )
    month_income = sum(
        t["amount"] for t in data["transactions"]
        if t["type"] == "income" and t["date"].startswith(ym)
    )

    credit_cards = []
    for a in data["accounts"]:
        if a["type"] == "liability" and a.get("class") == "current":
            pay_from = get_account(data, a.get("payFromAccountId"))
            credit_cards.append({
                "name": a["name"], "balance": a["balance"],
                "payDay": a.get("payDay"),
                "payFromAccount": pay_from["name"] if pay_from else "",
            })

    # 持ってるお金から開始
    running = hand
    months = []

    for offset in range(3):
        m = today.month + offset
        y = today.year
        while m > 12:
            m -= 12
            y += 1
        month_key = f"{y}-{m:02d}"

        events = build_cashflow_events(data, month_key, offset, today)

        month_events = []
        for e in events:
            if not e.get("cc"):
                running += e["amount"]
            month_events.append({
                "date": f"{m}/{e['day']}",
                "name": e["name"],
                "amount": e["amount"],
                "type": e["type"],
                "account": e["account"],
                "running": running,
                "cc": e.get("cc", False),
            })

        month_expense = sum(-e["amount"] for e in events if e["amount"] < 0)
        month_inc = sum(e["amount"] for e in events if e["amount"] > 0)

        months.append({
            "label": f"{y}年{m}月",
            "events": month_events,
            "endBalance": running,
            "totalExpense": month_expense,
            "totalIncome": month_inc,
        })

    return jsonify({
        "hand": hand,
        "currentAssets": current_assets,
        "currentLiabilities": current_liabilities,
        "currentNet": current_assets - current_liabilities,
        "creditCards": credit_cards,
        "monthExpenses": month_expenses,
        "monthIncome": month_income,
        "months": months,
    })


# ─── P/L ───

@app.route("/api/pl", methods=["GET"])
def get_pl():
    data = load_data()
    ym = request.args.get("month", date.today().strftime("%Y-%m"))
    expenses_by_cat = {}
    income_by_cat = {}
    # 階層: category > tag > schedule > amount
    expense_detail = {}  # {cat: {tag: {schedule: amount}}}
    income_detail = {}
    for t in data["transactions"]:
        if not t["date"].startswith(ym):
            continue
        cat = t.get("category", "その他")
        tags = t.get("tags", [])
        schedule = t.get("schedule", "") or ""
        tag_key = ", ".join(tags) if tags else "(タグなし)"
        sch_key = schedule if schedule else "(予定なし)"
        if t["type"] == "cc_detail":
            # CC明細のみ費用として計上（残高への影響なし）
            expenses_by_cat[cat] = expenses_by_cat.get(cat, 0) + t["amount"]
            expense_detail.setdefault(cat, {}).setdefault(tag_key, {})
            expense_detail[cat][tag_key][sch_key] = expense_detail[cat][tag_key].get(sch_key, 0) + t["amount"]
        elif t["type"] == "expense":
            # 負債口座（CC）への支出はcc_detailで管理するためP/L除外
            acc = get_account(data, t.get("accountId"))
            if acc and acc["type"] == "liability":
                pass  # スキップ
            else:
                expenses_by_cat[cat] = expenses_by_cat.get(cat, 0) + t["amount"]
                expense_detail.setdefault(cat, {}).setdefault(tag_key, {})
                expense_detail[cat][tag_key][sch_key] = expense_detail[cat][tag_key].get(sch_key, 0) + t["amount"]
        elif t["type"] == "income":
            income_by_cat[cat] = income_by_cat.get(cat, 0) + t["amount"]
            income_detail.setdefault(cat, {}).setdefault(tag_key, {})
            income_detail[cat][tag_key][sch_key] = income_detail[cat][tag_key].get(sch_key, 0) + t["amount"]
    # CC払いの固定費を当月P/Lに自動計上
    today = date.today()
    today_ym = today.strftime("%Y-%m")
    pl_fc_by_acc = {}  # {acc_id: sum} 固定費CC分の合計（unjournaled計算用）
    for fc in data["fixedCosts"]:
        fc_acc = get_account(data, fc.get("accountId"))
        if not fc_acc or not is_cc_account(fc_acc):
            continue
        # 当月に発生済みか判定（当月: day<=今日, 過去月: 全件）
        if ym == today_ym and fc["day"] > today.day:
            continue
        cat = fc.get("category", "雑費")
        tags = fc.get("tags", [])
        tag_key = ", ".join(tags) if tags else "(タグなし)"
        expenses_by_cat[cat] = expenses_by_cat.get(cat, 0) + fc["amount"]
        expense_detail.setdefault(cat, {}).setdefault(tag_key, {})
        expense_detail[cat][tag_key]["(固定費)"] = expense_detail[cat][tag_key].get("(固定費)", 0) + fc["amount"]
        acc_id = fc_acc["id"]
        pl_fc_by_acc[acc_id] = pl_fc_by_acc.get(acc_id, 0) + fc["amount"]

    # CC未仕訳: 当月はCC残高ベース、過去月はexpense取引ベースで計算
    today = date.today()
    today_ym = today.strftime("%Y-%m")
    cc_unsorted = 0
    for a in data["accounts"]:
        if not is_cc_account(a):
            continue
        if ym == today_ym:
            # 当月: CC残高から算出、cc_detailは請求サイクル内のみ
            cc_base = a["balance"]
            cycle_start = get_cc_cycle_start(a, today).isoformat()
            cc_det_sum = sum(
                t["amount"] for t in data["transactions"]
                if t["type"] == "cc_detail" and t.get("accountId") == a["id"]
                and cycle_start <= t["date"] <= today.isoformat()
            )
        else:
            # 過去月: その月のexpense取引・cc_detailから算出
            cc_base = sum(
                t["amount"] for t in data["transactions"]
                if t["type"] == "expense" and t.get("accountId") == a["id"]
                and t["date"].startswith(ym)
            )
            cc_det_sum = sum(
                t["amount"] for t in data["transactions"]
                if t["type"] == "cc_detail" and t.get("accountId") == a["id"]
                and t["date"].startswith(ym)
            )
        fc_sum = pl_fc_by_acc.get(a["id"], 0)
        unsorted = cc_base - cc_det_sum - fc_sum
        if unsorted > 0:
            cc_unsorted += unsorted
    if cc_unsorted > 0:
        expenses_by_cat["雑費"] = expenses_by_cat.get("雑費", 0) + cc_unsorted
        expense_detail.setdefault("雑費", {}).setdefault("(タグなし)", {})
        expense_detail["雑費"]["(タグなし)"]["CC未仕訳"] = expense_detail["雑費"]["(タグなし)"].get("CC未仕訳", 0) + cc_unsorted

    total_income = sum(income_by_cat.values())
    total_expenses = sum(expenses_by_cat.values())
    return jsonify({
        "month": ym,
        "income": income_by_cat,
        "expenses": expenses_by_cat,
        "expenseDetail": expense_detail,
        "incomeDetail": income_detail,
        "totalIncome": total_income,
        "totalExpenses": total_expenses,
        "netIncome": total_income - total_expenses,
        "ccUnsorted": cc_unsorted,
    })


# ─── B/S ───

@app.route("/api/bs", methods=["GET"])
def get_bs():
    data = load_data()
    current_assets = [{"name": a["name"], "balance": a["balance"]} for a in data["accounts"] if a["type"] == "asset" and a.get("class") == "current"]
    long_assets = [{"name": a["name"], "balance": a["balance"]} for a in data["accounts"] if a["type"] == "asset" and a.get("class") == "long"]
    current_liabilities = [{"name": a["name"], "balance": a["balance"]} for a in data["accounts"] if a["type"] == "liability" and a.get("class") == "current"]
    long_liabilities = [{"name": a["name"], "balance": a["balance"]} for a in data["accounts"] if a["type"] == "liability" and a.get("class") == "long"]

    total_ca = sum(a["balance"] for a in current_assets)
    total_la = sum(a["balance"] for a in long_assets)
    total_cl = sum(a["balance"] for a in current_liabilities)
    total_ll = sum(a["balance"] for a in long_liabilities)
    total_assets = total_ca + total_la
    total_liabilities = total_cl + total_ll

    return jsonify({
        "currentAssets": current_assets,
        "longAssets": long_assets,
        "currentLiabilities": current_liabilities,
        "longLiabilities": long_liabilities,
        "totalCurrentAssets": total_ca,
        "totalLongAssets": total_la,
        "totalCurrentLiabilities": total_cl,
        "totalLongLiabilities": total_ll,
        "totalAssets": total_assets,
        "totalLiabilities": total_liabilities,
        "netWorth": total_assets - total_liabilities,
    })


# ─── Calendar ───

@app.route("/api/calendar", methods=["GET"])
def get_calendar():
    data = load_data()
    month_str = request.args.get("month", date.today().strftime("%Y-%m"))
    year, month = map(int, month_str.split("-"))
    today = date.today()
    this_ym = today.strftime("%Y-%m")

    num_days = cal.monthrange(year, month)[1]
    first_dow = (date(year, month, 1).weekday() + 1) % 7

    current_assets = sum(a["balance"] for a in data["accounts"]
                         if a["type"] == "asset" and a.get("class") == "current")

    pending_income, pending_ids = calc_pending_income(data)
    hand = current_assets - pending_income

    # 対象月の記録済み取引を日別に集計
    tx_by_day = {}
    for tx in data["transactions"]:
        if tx["date"].startswith(month_str) and tx["type"] in ("expense", "income", "cc_detail"):
            d = int(tx["date"][8:10])
            tx_by_day.setdefault(d, []).append(tx)

    def build_tx_events(txs):
        """取引リストからイベントリストを構築（同じ予定はグループ化）"""
        schedule_groups = {}
        no_schedule = []
        for tx in txs:
            amt = -tx["amount"] if tx["type"] in ("expense", "cc_detail") else tx["amount"]
            sch = tx.get("schedule", "")
            if sch:
                if sch not in schedule_groups:
                    schedule_groups[sch] = {"amount": 0, "type": tx["type"]}
                schedule_groups[sch]["amount"] += amt
            else:
                cat = tx.get("category", "")
                memo = tx.get("memo", "")
                parts = [p for p in [cat, memo] if p]
                no_schedule.append({
                    "name": " - ".join(parts) if parts else "取引",
                    "amount": amt, "type": tx["type"], "actual": True,
                })
        evts = []
        for sch, g in schedule_groups.items():
            evts.append({"name": sch, "amount": g["amount"], "type": g["type"], "actual": True})
        evts.extend(no_schedule)
        return evts

    if month_str < this_ym:
        # ── 過去月: リバース＆リプレイ ──
        # 過去月も hand ベースで逆算
        start_balance = hand
        # 対象月以降の全取引を逆算してスタートを求める（資産口座ベース）
        for tx in data["transactions"]:
            if tx["date"] >= f"{year}-{month:02d}-01" and tx["type"] in ("expense", "income"):
                acc = get_account(data, tx.get("accountId"))
                if acc and acc["type"] == "asset" and acc.get("class") == "current":
                    if tx["type"] == "expense":
                        start_balance += tx["amount"]
                    elif tx["type"] == "income":
                        start_balance -= tx["amount"]

        running = start_balance
        days = []
        for d in range(1, num_days + 1):
            actual_txs = tx_by_day.get(d, [])
            events = build_tx_events(actual_txs)
            for tx in actual_txs:
                acc = get_account(data, tx.get("accountId"))
                if acc and acc["type"] == "asset" and acc.get("class") == "current":
                    amt = -tx["amount"] if tx["type"] == "expense" else tx["amount"]
                    running += amt
            days.append({
                "day": d, "events": events, "balance": running,
                "isToday": False, "isPast": True,
            })

        return jsonify({
            "month": month_str, "label": f"{year}年{month}月",
            "firstDow": first_dow, "numDays": num_days,
            "startBalance": start_balance, "endBalance": running,
            "days": days,
        })

    # ── 当月 or 未来月 ──
    # 持ってるお金（hand）からスタート
    running = hand

    # 未来月の場合: 今日〜対象月初の間のイベントを順算
    if month_str > this_ym:
        # 当月の残りイベントを適用
        events_remaining = build_cashflow_events(data, this_ym, 0, today)
        for e in events_remaining:
            if not e.get("cc"):
                running += e["amount"]
        # 間の月
        cm, cy = today.month + 1, today.year
        while True:
            if cm > 12:
                cm -= 12
                cy += 1
            if cy > year or (cy == year and cm >= month):
                break
            mk = f"{cy}-{cm:02d}"
            inter_events = build_cashflow_events(data, mk, 1, today)
            for e in inter_events:
                if not e.get("cc"):
                    running += e["amount"]
            cm += 1

    start_balance = running
    days = []
    for d in range(1, num_days + 1):
        day_date = date(year, month, d)
        events = []

        # 記録済み取引を表示
        actual_txs = tx_by_day.get(d, [])
        if actual_txs:
            events = build_tx_events(actual_txs)
            for tx in actual_txs:
                # 未到着収入 → 残高に反映
                if tx["id"] in pending_ids:
                    running += tx["amount"]
                # cc_detailは情報表示のみ。残高への影響なし（CC残高はCC雑費で計上）

        # CC未仕訳雑費（当月・今日に計上）
        if month_str == this_ym and d == today.day:
            today_iso = today.isoformat()
            for a in data["accounts"]:
                if not is_cc_account(a):
                    continue
                cycle_start = get_cc_cycle_start(a, today).isoformat()
                cc_det_sum = sum(t["amount"] for t in data["transactions"]
                                 if t["type"] == "cc_detail" and t.get("accountId") == a["id"]
                                 and cycle_start <= t["date"] <= today_iso)
                cc_fc_sum = sum(fc["amount"] for fc in data["fixedCosts"]
                                if fc.get("accountId") == a["id"] and fc["day"] <= today.day)
                unjournaled = a["balance"] - cc_det_sum - cc_fc_sum
                if unjournaled > 0:
                    events.append({
                        "name": f"{a['name']} 雑費",
                        "amount": -unjournaled,
                        "type": "expense",
                        "actual": True,
                        "cc": False,
                    })
                    running -= unjournaled

        # 未来日: スケジュールイベント
        is_future = (month_str > this_ym) or (month_str == this_ym and day_date > today)
        if is_future:
            # 未来の負債口座取引（支払い予定など、CC以外）→ キャッシュアウト
            for tx in actual_txs:
                if tx["id"] in pending_ids:
                    continue
                acc = get_account(data, tx.get("accountId"))
                if acc and acc["type"] == "liability" and not is_cc_account(acc):
                    if tx["type"] == "expense":
                        running -= tx["amount"]

            # 固定費（CC固定費も含めてすべて残高に影響）
            for fc in data["fixedCosts"]:
                if fc["day"] == d:
                    events.append({
                        "name": fc["name"], "amount": -fc["amount"],
                        "type": "expense", "actual": False, "cc": False,
                    })
                    running -= fc["amount"]

            # 定期収入
            for inc in data["incomeSchedule"]:
                if inc["day"] == d:
                    events.append({
                        "name": inc["name"], "amount": inc["amount"],
                        "type": "income", "actual": False, "cc": False,
                    })
                    running += inc["amount"]

        show_balance = day_date >= today if month_str == this_ym else True
        days.append({
            "day": d, "events": events,
            "balance": running if show_balance else None,
            "isToday": day_date == today,
            "isPast": day_date < today and month_str == this_ym,
        })

    return jsonify({
        "month": month_str, "label": f"{year}年{month}月",
        "firstDow": first_dow, "numDays": num_days,
        "startBalance": start_balance, "endBalance": running,
        "days": days,
    })


if __name__ == "__main__":
    print("家計簿アプリを起動中...")
    print("ブラウザで http://localhost:8080 を開いてください")
    app.run(debug=True, port=8080)
