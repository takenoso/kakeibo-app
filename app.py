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


# ─── Pages ───

@app.route("/")
def index():
    return render_template("index.html")


# ─── Accounts ───

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    data = load_data()
    return jsonify(data["accounts"])


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
        acc["balance"] = int(body["balance"])
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
    }
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

@app.route("/api/summary", methods=["GET"])
def get_summary():
    data = load_data()
    today = date.today()
    ym = f"{today.year}-{today.month:02d}"

    # 流動資産/負債のみで「使えるお金」を計算
    current_assets = sum(a["balance"] for a in data["accounts"] if a["type"] == "asset" and a.get("class") == "current")
    current_liabilities = sum(a["balance"] for a in data["accounts"] if a["type"] == "liability" and a.get("class") == "current")
    long_assets = sum(a["balance"] for a in data["accounts"] if a["type"] == "asset" and a.get("class") == "long")
    long_liabilities = sum(a["balance"] for a in data["accounts"] if a["type"] == "liability" and a.get("class") == "long")

    total_assets = current_assets + long_assets
    total_liabilities = current_liabilities + long_liabilities
    net_worth = total_assets - total_liabilities
    current_net = current_assets - current_liabilities

    month_expenses = sum(
        t["amount"] for t in data["transactions"]
        if t["type"] in ("expense", "cc_detail") and t["date"].startswith(ym)
    )
    month_income = sum(
        t["amount"] for t in data["transactions"]
        if t["type"] == "income" and t["date"].startswith(ym)
    )

    # CC払いの固定費はCC負債に既に含まれるため除外
    remaining_fixed = 0
    for fc in data["fixedCosts"]:
        if fc["day"] > today.day:
            acc = get_account(data, fc.get("accountId"))
            if acc and acc["type"] == "asset":
                remaining_fixed += fc["amount"]
    remaining_income = sum(inc["amount"] for inc in data["incomeSchedule"] if inc["day"] > today.day)
    spendable = current_net - remaining_fixed + remaining_income

    return jsonify({
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
        "monthNet": month_income - month_expenses,
        "remainingFixed": remaining_fixed,
        "remainingIncome": remaining_income,
        "spendable": spendable,
    })


# ─── Cash Flow ───

@app.route("/api/cashflow", methods=["GET"])
def get_cashflow():
    data = load_data()
    today = date.today()
    ym = f"{today.year}-{today.month:02d}"

    current_assets = sum(a["balance"] for a in data["accounts"] if a["type"] == "asset" and a.get("class") == "current")
    current_liabilities = sum(a["balance"] for a in data["accounts"] if a["type"] == "liability" and a.get("class") == "current")
    current_net = current_assets - current_liabilities

    total_assets = sum(a["balance"] for a in data["accounts"] if a["type"] == "asset")
    total_liabilities = sum(a["balance"] for a in data["accounts"] if a["type"] == "liability")

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
                "name": a["name"],
                "balance": a["balance"],
                "payDay": a.get("payDay"),
                "payFromAccount": pay_from["name"] if pay_from else "",
            })

    months = []
    running = current_net

    for offset in range(3):
        m = today.month + offset
        y = today.year
        while m > 12:
            m -= 12
            y += 1

        events = []

        for fc in data["fixedCosts"]:
            day = fc["day"]
            if offset == 0 and day <= today.day:
                continue
            acc = get_account(data, fc.get("accountId"))
            is_cc = acc and acc["type"] == "liability"
            events.append({
                "day": day,
                "name": fc["name"],
                "amount": -fc["amount"],
                "type": "expense",
                "account": acc["name"] if acc else "",
                "cc": is_cc,
            })

        for inc in data["incomeSchedule"]:
            day = inc["day"]
            if offset == 0 and day <= today.day:
                continue
            acc = get_account(data, inc.get("accountId"))
            events.append({
                "day": day,
                "name": inc["name"],
                "amount": inc["amount"],
                "type": "income",
                "account": acc["name"] if acc else "",
                "cc": False,
            })

        events.sort(key=lambda e: e["day"])

        month_events = []
        for e in events:
            # CC払いの固定費はCC負債に既に含まれるため残高に影響しない
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
        "currentNet": current_net,
        "currentAssets": current_assets,
        "currentLiabilities": current_liabilities,
        "totalAssets": total_assets,
        "totalLiabilities": total_liabilities,
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
    for t in data["transactions"]:
        if not t["date"].startswith(ym):
            continue
        cat = t.get("category", "その他")
        if t["type"] in ("expense", "cc_detail"):
            expenses_by_cat[cat] = expenses_by_cat.get(cat, 0) + t["amount"]
        elif t["type"] == "income":
            income_by_cat[cat] = income_by_cat.get(cat, 0) + t["amount"]
    total_income = sum(income_by_cat.values())
    total_expenses = sum(expenses_by_cat.values())
    return jsonify({
        "month": ym,
        "income": income_by_cat,
        "expenses": expenses_by_cat,
        "totalIncome": total_income,
        "totalExpenses": total_expenses,
        "netIncome": total_income - total_expenses,
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

    num_days = cal.monthrange(year, month)[1]
    first_dow = (date(year, month, 1).weekday() + 1) % 7  # Sunday=0

    # 流動純資産（今日時点）
    cn = (
        sum(a["balance"] for a in data["accounts"]
            if a["type"] == "asset" and a.get("class") == "current")
        - sum(a["balance"] for a in data["accounts"]
              if a["type"] == "liability" and a.get("class") == "current")
    )

    # 今月の実取引を日別に集計
    tx_by_day = {}
    for tx in data["transactions"]:
        if tx["date"].startswith(month_str) and tx["type"] in ("expense", "income"):
            d = int(tx["date"][8:10])
            tx_by_day.setdefault(d, []).append(tx)

    target_start = date(year, month, 1)

    if target_start <= today:
        # 現在月 or 過去月: 今日の残高から逆算
        start_balance = cn
        for tx in data["transactions"]:
            td = tx["date"]
            if td >= target_start.isoformat() and td <= today.isoformat():
                if tx["type"] == "expense":
                    start_balance += tx["amount"]
                elif tx["type"] == "income":
                    start_balance -= tx["amount"]
    else:
        # 未来月: 今日の残高から順算
        start_balance = cn
        # 今月の残りの予定（CC払い固定費は除外）
        for fc in data["fixedCosts"]:
            if fc["day"] > today.day:
                fc_acc = get_account(data, fc.get("accountId"))
                if fc_acc and fc_acc["type"] == "asset":
                    start_balance -= fc["amount"]
        for inc in data["incomeSchedule"]:
            if inc["day"] > today.day:
                start_balance += inc["amount"]
        # 間の月（フル適用、CC払い固定費は除外）
        cm = today.month + 1
        cy = today.year
        while True:
            if cm > 12:
                cm -= 12
                cy += 1
            if cy > year or (cy == year and cm >= month):
                break
            for fc in data["fixedCosts"]:
                fc_acc = get_account(data, fc.get("accountId"))
                if fc_acc and fc_acc["type"] == "asset":
                    start_balance -= fc["amount"]
            for inc in data["incomeSchedule"]:
                start_balance += inc["amount"]
            cm += 1

    # 日別データ構築
    running = start_balance
    days = []
    for d in range(1, num_days + 1):
        day_date = date(year, month, d)
        events = []

        if day_date <= today:
            # 過去/当日: 実取引
            for tx in tx_by_day.get(d, []):
                amt = -tx["amount"] if tx["type"] == "expense" else tx["amount"]
                cat = tx.get("category", "")
                sch = tx.get("schedule", "")
                memo = tx.get("memo", "")
                parts = [p for p in [cat, sch, memo] if p]
                events.append({
                    "name": " - ".join(parts) if parts else "取引",
                    "amount": amt,
                    "type": tx["type"],
                    "actual": True,
                })
                running += amt
        else:
            # 未来: 予定イベント
            for fc in data["fixedCosts"]:
                if fc["day"] == d:
                    fc_acc = get_account(data, fc.get("accountId"))
                    is_cc = fc_acc and fc_acc["type"] == "liability"
                    events.append({
                        "name": fc["name"],
                        "amount": -fc["amount"],
                        "type": "expense",
                        "actual": False,
                        "cc": is_cc,
                    })
                    if not is_cc:
                        running -= fc["amount"]
            for inc in data["incomeSchedule"]:
                if inc["day"] == d:
                    events.append({
                        "name": inc["name"],
                        "amount": inc["amount"],
                        "type": "income",
                        "actual": False,
                        "cc": False,
                    })
                    running += inc["amount"]

        days.append({
            "day": d,
            "events": events,
            "balance": running,
            "isToday": day_date == today,
            "isPast": day_date < today,
        })

    return jsonify({
        "month": month_str,
        "label": f"{year}年{month}月",
        "firstDow": first_dow,
        "numDays": num_days,
        "startBalance": start_balance,
        "endBalance": running,
        "days": days,
    })


if __name__ == "__main__":
    print("家計簿アプリを起動中...")
    print("ブラウザで http://localhost:8080 を開いてください")
    app.run(debug=True, port=8080)
