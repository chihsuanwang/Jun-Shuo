"""會計科目表(2026-10-16 起,依同事「郡碩農創_記帳系統_1015.xlsx」帳務設定表)。

科目存在資料庫 `account` 表,「會計科目設定」頁(/accounts)可以新增、改名、停用,
系統各處的科目名稱、下拉選單都從這裡讀,不再寫死在程式裡。`DEFAULT_CHART` 只用在
第一次建表(或舊資料庫補表)時灌初值;之後以資料庫為準。

分組用帳務設定表的英文字母(C 現金銀行、UA 銷貨收入、VD 營運費用…),每種交易類型
的下拉選單只列它用得到的組(見帳務設定表左半邊)。
"""
from db import q, q1, execute

GROUPS = [
    ("C",  "現金 / 銀行"),
    ("E",  "應收帳款"),
    ("F",  "存貨(目前沒有存貨,先保留)"),
    ("G",  "進項 / 留抵稅額"),
    ("J",  "股東往來(資產)"),
    ("K",  "機器設備"),
    ("N",  "應付帳款"),
    ("P",  "銷項稅額 / 股東往來(負債)"),
    ("T",  "權益"),
    ("UA", "銷貨收入"),
    ("VA", "採購成本"),
    ("VC", "折舊"),
    ("VD", "營運費用"),
    ("UB", "業外收入"),
    ("VE", "其他支出"),
    ("其他", "報表用(算出來的,不能記帳)"),
]
GROUP_LABEL = dict(GROUPS)

DEFAULT_CHART = [
    ("C", "1102", "現金"), ("C", "1103-01", "合庫"), ("C", "1103-02", "凱基"), ("C", "1103-03", "台新"),
    ("E", "1172", "應收帳款"),
    ("F", "1315-01", "原料-龍眼鮮果"), ("F", "1311-01", "製成品-龍眼乾"), ("F", "1311-02", "製成品-龍眼肉"),
    ("F", "1301-01", "商品-龍眼花"), ("F", "1301-02", "商品-龍眼蜂蜜"),
    ("G", "1423", "進項稅額"), ("G", "1424", "留抵稅額"),
    ("J", "1996", "股東往來(資產)"),
    ("K", "1616", "機器設備"), ("K", "1618", "累計折舊-機器設備"),
    ("N", "2171", "應付帳款"),
    ("P", "2214", "銷項稅額"), ("P", "2215", "應付營業稅"), ("P", "2655", "股東往來(負債)"),
    ("T", "3110", "實收資本(股本)"), ("T", "3211", "資本公積"), ("T", "3310-03", "法定盈餘公積"),
    ("T", "3351-03", "累積盈虧"), ("T", "3353", "本期損益"),
    ("UA", "4111-01", "銷貨收入-龍眼鮮果"), ("UA", "4111-02", "銷貨收入-龍眼乾"),
    ("UA", "4111-03", "銷貨收入-龍眼肉"), ("UA", "4111-04", "銷貨收入-龍眼花"),
    ("UA", "4111-05", "銷貨收入-龍眼蜂蜜"),
    ("VA", "5111-01", "採購成本-龍眼鮮果"), ("VA", "5111-02", "採購成本-龍眼乾"),
    ("VA", "5111-03", "採購成本-龍眼肉"), ("VA", "5111-04", "採購成本-龍眼花"),
    ("VA", "5111-05", "採購成本-龍眼蜂蜜"), ("VA", "5111-2150-03", "原料報廢損失"),
    ("VC", "6124", "折舊"),
    ("VD", "5111-2408", "製造費用-包裝費"), ("VD", "5111-2417-01", "製造費用-其他消耗材料及工具"),
    ("VD", "6110-01", "薪資費用-薪資"), ("VD", "6110-02", "薪資費用-獎金"), ("VD", "6110-03", "薪資費用-退休金"),
    ("VD", "6111", "租金支出"), ("VD", "6112", "文具用品"), ("VD", "6113", "旅費"), ("VD", "6114", "運費"), ("VD", "6115", "郵電費"),
    ("VD", "6116", "修繕費"), ("VD", "6117", "廣告費"), ("VD", "6118", "水電瓦斯費"), ("VD", "6119", "保險費"),
    ("VD", "6120", "交際費"), ("VD", "6121", "捐贈"), ("VD", "612901", "研究發展費"),
    ("VD", "6188-01", "其他費用-勞務費"), ("VD", "6188-02", "其他費用-檢驗費"), ("VD", "6188-03", "其他費用-規費"),
    ("VD", "6188-04", "其他費用-手續費"), ("VD", "6188-05", "其他費用-印刷品"), ("VD", "6188-98", "其他費用-盤虧"),
    ("VD", "6188-99", "其他費用-其他"),
    ("UB", "7101", "利息收入"), ("UB", "7250", "生物資產淨FV利益"), ("UB", "7190-01", "其他收入-政府補助"),
    ("UB", "7190-98", "其他收入-盤盈"), ("UB", "7190-99", "其他收入-其他"),
    ("VE", "7510", "利息費用"), ("VE", "7950", "所得稅費用"),
    ("其他", "5950", "營業毛利"),
]

# 只在報表上算出來、不能拿來記帳的科目(不會出現在任何下拉選單,試算表也不補 0 列)
COMPUTED_CODES = {"3353", "5950"}

# ---------- 科目階層(2026-10-23) ----------------------------------------
#   account.parent_code = 上層科目;account.is_summary = 1 是「彙總科目」:不能記帳(不出現在任何記帳下拉),
#   金額 = 底下所有科目加總(可以多層)。新增科目時用代碼自動建議上層(1103-04 → 1103),使用者可以改。
#   初始的彙總科目(名稱可在會計科目設定頁改)。5111 叫「銷貨成本」:底下有採購成本 5111-01~05,
#   也有同仁 10/20 加的 5111-2150-03 原料報廢損失、5111-2408 / 5111-2417-01 製造費用——都算營業成本。
SUMMARY_DEFAULTS = [
    ("C", "1103", "銀行存款"), ("F", "1301", "商品存貨"), ("F", "1311", "製成品"),
    ("UA", "4111", "銷貨收入"), ("VA", "5111", "銷貨成本"), ("VD", "6110", "薪資費用"),
    ("VD", "6188", "其他費用"), ("UB", "7190", "其他收入"),
]


def suggest_parent(code, summaries=None):
    """用代碼找最接近的彙總科目當上層:去掉最後一段「-xx」一直往上找(5111-2150-03 → 5111-2150 → 5111)。"""
    if summaries is None:
        summaries = {r["code"] for r in q("SELECT code FROM account WHERE is_summary=1")}
    parts = (code or "").split("-")
    while len(parts) > 1:
        parts.pop()
        cand = "-".join(parts)
        if cand in summaries and cand != code:
            return cand
    return None


def setup_hierarchy():
    """建初始彙總科目,沒有上層的科目照代碼自動掛上去(只掛到彙總科目底下)。"""
    for i, (g, code, name) in enumerate(SUMMARY_DEFAULTS):
        if not q1("SELECT 1 FROM account WHERE code=?", (code,)):
            execute("INSERT INTO account(code,name,grp,sort,is_summary) VALUES(?,?,?,?,1)", (code, name, g, -100 + i))
    summaries = {r["code"] for r in q("SELECT code FROM account WHERE is_summary=1")}
    for r in q("SELECT code FROM account WHERE parent_code IS NULL"):
        p = suggest_parent(r["code"], summaries)
        if p:
            execute("UPDATE account SET parent_code=? WHERE code=?", (p, r["code"]))


def descendants(code):
    """這個科目底下所有層的子科目代碼(不含自己)。"""
    kids = {}
    for r in q("SELECT code, parent_code FROM account WHERE parent_code IS NOT NULL"):
        kids.setdefault(r["parent_code"], []).append(r["code"])
    out, stack = [], list(kids.get(code, []))
    while stack:
        c = stack.pop()
        out.append(c)
        stack.extend(kids.get(c, []))
    return out


def tree_order(rows, code_key="code"):
    """把一串科目(dict,要有 code_key、parent_code)排成樹:上層在前、子科目縮排在後,同層照代碼排。
    每列加 level(0 = 最上層)。上層不在這串裡的就當最上層。"""
    by = {r[code_key]: r for r in rows}
    kids = {}
    roots = []
    for r in rows:
        p = r.get("parent_code")
        if p and p in by and p != r[code_key]:
            kids.setdefault(p, []).append(r)
        else:
            roots.append(r)
    out, seen = [], set()

    def walk(r, lvl):
        if r[code_key] in seen:
            return
        seen.add(r[code_key])
        r["level"] = lvl
        out.append(r)
        for k in sorted(kids.get(r[code_key], []), key=lambda x: x[code_key]):
            walk(k, lvl + 1)
    for r in sorted(roots, key=lambda x: x[code_key]):
        walk(r, 0)
    return out


# ---------- 產品線(2026-10-24) -------------------------------------------
#   賣出時銷貨收入 / 採購成本記到哪個科目,靠商品的「產品線」決定(ledger.REVENUE_ACCOUNTS / COST_ACCOUNTS
#   用產品線**名稱**對到 4111-0x / 5111-0x)。所以產品線跟會計科目表一樣是基本設定,空白資料庫也一定要有——
#   10/23 Render 用 seed.py --empty 清空後沒有產品線,商品只能選「未分類」,收入 / 成本全被合到預設科目。
DEFAULT_PRODUCT_LINES = [("龍眼", ["龍眼鮮果", "龍眼乾", "龍眼肉", "龍眼花"]), ("蜂蜜", ["蜂蜜"])]


def ensure_product_lines(cx=None):
    """product_group 是空的就建預設的事業別 + 產品線(已經有就不動,使用者改過的不會被蓋)。cx 給 seed.py 用。"""
    run = (lambda sql, a=(): cx.execute(sql, a)) if cx is not None else None
    count = (cx.execute("SELECT COUNT(*) FROM product_group").fetchone()[0] if cx is not None
             else q1("SELECT COUNT(*) n FROM product_group")["n"])
    if count:
        return
    i = 0
    for srt, (bu_name, groups) in enumerate(DEFAULT_PRODUCT_LINES, start=1):
        if cx is not None:
            row = cx.execute("SELECT bu_id FROM business_unit WHERE name=?", (bu_name,)).fetchone()
            bu_id = row[0] if row else run("INSERT INTO business_unit(name,sort) VALUES(?,?)", (bu_name, srt)).lastrowid
        else:
            row = q1("SELECT bu_id FROM business_unit WHERE name=?", (bu_name,))
            bu_id = row.get("bu_id") or execute("INSERT INTO business_unit(name,sort) VALUES(?,?)", (bu_name, srt))
        for g in groups:
            if cx is not None:
                run("INSERT INTO product_group(bu_id,name,sort) VALUES(?,?,?)", (bu_id, g, i))
            else:
                execute("INSERT INTO product_group(bu_id,name,sort) VALUES(?,?,?)", (bu_id, g, i))
            i += 1


def ensure_chart(cx=None):
    """account 表是空的就灌 DEFAULT_CHART。cx 給 seed.py 用(它有自己的連線)。"""
    if cx is not None:
        if cx.execute("SELECT COUNT(*) FROM account").fetchone()[0]:
            return
        for i, (g, code, name) in enumerate(DEFAULT_CHART):
            cx.execute("INSERT INTO account(code,name,grp,sort) VALUES(?,?,?,?)", (code, name, g, i))
        return
    if q1("SELECT COUNT(*) n FROM account")["n"]:
        return
    for i, (g, code, name) in enumerate(DEFAULT_CHART):
        execute("INSERT INTO account(code,name,grp,sort) VALUES(?,?,?,?)", (code, name, g, i))


# 2026-10-20 同仁回覆「待同仁確認」新增的科目。已經在用的資料庫(含 Render 網站)開機時補上一次,
# 用 PRAGMA user_version 記「補過了」,之後使用者自己刪掉也不會一直被補回來。
# 版本 2(2026-10-23):建初始彙總科目、掛上層(setup_hierarchy),之後使用者改名 / 搬移都不會被蓋回來。
CHART_ADDITIONS = {
    1: ["2215", "5111-2150-03", "5111-2408", "5111-2417-01", "6111"],
    2: setup_hierarchy,
}


def apply_chart_additions():
    ver = q1("PRAGMA user_version").get("user_version") or 0
    for v in sorted(k for k in CHART_ADDITIONS if k > ver):
        if callable(CHART_ADDITIONS[v]):
            CHART_ADDITIONS[v]()
            execute(f"PRAGMA user_version = {int(v)}")
            continue
        for code in CHART_ADDITIONS[v]:
            if q1("SELECT 1 FROM account WHERE code=?", (code,)):
                continue
            i, (g, _, name) = next((i, x) for i, x in enumerate(DEFAULT_CHART) if x[1] == code)
            execute("INSERT INTO account(code,name,grp,sort) VALUES(?,?,?,?)", (code, name, g, i))
        execute(f"PRAGMA user_version = {int(v)}")


def name_of(code, default=None):
    r = q1("SELECT name FROM account WHERE code=?", (code,))
    return r.get("name") or default or code


def accounts(groups=None, active_only=True, postable_only=False, include_summary=False):
    """依分組列科目(下拉選單用),回傳 list of dict(code,name,grp,active,parent_code,is_summary)。groups=None 是全部。
    預設**不含彙總科目**(彙總科目不能記帳,記帳下拉一律不能出現);科目設定 / 試算表 / 科目明細才傳 include_summary=True。"""
    where, args = ["1=1"], []
    if groups:
        where.append(f"grp IN ({','.join('?' * len(groups))})"); args += list(groups)
    if active_only:
        where.append("active=1")
    if not include_summary:
        where.append("is_summary=0")
    rows = q(f"""SELECT code, name, grp, active, parent_code, is_summary FROM account
                 WHERE {' AND '.join(where)} ORDER BY code""", args)
    if postable_only:
        rows = [r for r in rows if r["code"] not in COMPUTED_CODES]
    return rows


def bank_accounts(active_only=True):
    """C 組裡的銀行(不含 1102 現金)。"""
    return [r for r in accounts(["C"], active_only) if r["code"] != "1102"]


def cash_account(payment_account):
    """付款 / 收款帳戶名稱 → (科目代碼, 科目名稱)。留白或「現金」= 1102。
    名稱在 C 組找不到時,自動新增一個 1103-NN(接著目前最大號往下編),代碼一旦指派就不變。"""
    name = (payment_account or "").strip()
    if not name or name == "現金":
        return ("1102", name_of("1102", "現金"))
    r = q1("SELECT code, name FROM account WHERE grp='C' AND is_summary=0 AND name=?", (name,))
    if r:
        return (r["code"], r["name"])
    nums = [int(x["code"].split("-")[1]) for x in q("SELECT code FROM account WHERE code LIKE '1103-%'")
            if x["code"].split("-")[1].isdigit()]
    code = f"1103-{(max(nums) if nums else 0) + 1:02d}"
    sort = (q1("SELECT MAX(sort) m FROM account").get("m") or 0) + 1
    execute("INSERT INTO account(code,name,grp,sort,parent_code) VALUES(?,?,?,?,?)",
            (code, name, "C", sort, suggest_parent(code)))
    return (code, name)
