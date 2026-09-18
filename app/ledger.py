"""方案 B 試點:進貨 / 營運費用 自動過帳。

家易照舊填熟悉的業務表單,這裡依對照表把送出的內容組成一組複式分錄草稿,
存檔前在確認畫面給他看、可微調(app/templates/ledger_confirm.html)。
只接「進貨」(排除設備採購,那走固定資產)跟「營運費用」——訂單、固定資產購入、
折舊、退貨這批先不做。

對照表(PURCHASE_ACCOUNTS / EXPENSE_ACCOUNTS)是第一版、可調整的猜測,
不是跟會計師/同事對過的正式科目表,之後要改直接改這個檔案的常數就好,
比照 queries.OPEX_CATS 也是這樣讓人直接改的做法。
"""
from db import q, execute

CASH_ACCOUNT = ("1102", "現金")
TAX_INPUT_ACCOUNT = ("1150", "進項稅額")

# 進貨類別 -> 借方科目(存貨 / 採購成本類)。is_fixed_asset=1 的不查這張表——
# 設備採購不產生分錄,交給固定資產那條路。
PURCHASE_ACCOUNTS = {
    "原料":    ("1301", "存貨-原料"),
    "包材":    ("1302", "存貨-包材"),
    "委外加工": ("1303", "委外加工成本"),
    "服務":    ("5901", "其他採購成本-服務"),
    "其他":    ("5909", "其他採購成本"),
}

# 營運費用類別(對應 queries.OPEX_CATS)-> 借方費用科目
EXPENSE_ACCOUNTS = {
    "直接人工":   ("6111", "薪資費用-直接人工"),
    "田間管理":   ("6112", "田間管理費用"),
    "驗證費":     ("6120", "驗證費"),
    "研發":       ("6121", "研究發展費"),
    "人事":       ("6113", "薪資費用-人事"),
    "場地・倉儲": ("6130", "場地倉儲費用"),
    "行銷":       ("6140", "行銷費用"),
    "金流手續費": ("6150", "金流手續費"),
    "設備維護":   ("6160", "設備維護費"),
    "培訓":       ("6170", "培訓費"),
    "其他":       ("6190", "其他費用"),
}


def _cash_account(payment_account):
    name = (payment_account or "").strip()
    if not name or name == "現金":
        return CASH_ACCOUNT
    return ("1103", f"銀行存款-{name}")


def compose_purchase_entries(category, amount, tax_amount=0, payment_account=None, is_fixed_asset=False):
    """回傳一組分錄草稿(list of dict: account_code/account_name/debit/credit)。
    設備採購(is_fixed_asset)不在這批範圍,回傳空 list。"""
    if is_fixed_asset:
        return []
    amount = amount or 0
    tax_amount = tax_amount or 0
    code, name = PURCHASE_ACCOUNTS.get(category, PURCHASE_ACCOUNTS["其他"])
    cash_code, cash_name = _cash_account(payment_account)
    legs = [dict(account_code=code, account_name=name, debit=amount, credit=0)]
    if tax_amount:
        legs.append(dict(account_code=TAX_INPUT_ACCOUNT[0], account_name=TAX_INPUT_ACCOUNT[1],
                          debit=tax_amount, credit=0))
    legs.append(dict(account_code=cash_code, account_name=cash_name, debit=0, credit=amount + tax_amount))
    return legs


def compose_expense_entries(category, amount, tax_amount=0, payment_account=None):
    amount = amount or 0
    tax_amount = tax_amount or 0
    code, name = EXPENSE_ACCOUNTS.get(category, EXPENSE_ACCOUNTS["其他"])
    cash_code, cash_name = _cash_account(payment_account)
    legs = [dict(account_code=code, account_name=name, debit=amount, credit=0)]
    if tax_amount:
        legs.append(dict(account_code=TAX_INPUT_ACCOUNT[0], account_name=TAX_INPUT_ACCOUNT[1],
                          debit=tax_amount, credit=0))
    legs.append(dict(account_code=cash_code, account_name=cash_name, debit=0, credit=amount + tax_amount))
    return legs


def next_voucher_no(prefix, date):
    """prefix 依來源類別(P=進貨、E=營運費用),同一天同 prefix 序號累加。"""
    ymd = date.replace("-", "")
    like = f"{prefix}{ymd}-%"
    rows = q("SELECT voucher_no FROM ledger_entry WHERE voucher_no LIKE ? ORDER BY voucher_no DESC LIMIT 1",
             (like,))
    n = 1
    if rows:
        try:
            n = int(rows[0]["voucher_no"].rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            n = 1
    return f"{prefix}{ymd}-{n:03d}"


def save_voucher(source_type, source_id, date, legs, prefix, note=None):
    """整張傳票重開:先刪掉這個來源既有的分錄列,再依 legs 插入新的一組。
    legs 為空(例:設備採購)時只清空、不產生新傳票。"""
    execute("DELETE FROM ledger_entry WHERE source_type=? AND source_id=?", (source_type, source_id))
    if not legs:
        return None
    voucher_no = next_voucher_no(prefix, date)
    for leg in legs:
        execute("""INSERT INTO ledger_entry(voucher_no, entry_date, account_code, account_name,
                     debit, credit, source_type, source_id, note)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (voucher_no, date, leg["account_code"], leg["account_name"],
                 leg["debit"], leg["credit"], source_type, source_id, note))
    return voucher_no


def delete_voucher_for(source_type, source_id):
    execute("DELETE FROM ledger_entry WHERE source_type=? AND source_id=?", (source_type, source_id))
