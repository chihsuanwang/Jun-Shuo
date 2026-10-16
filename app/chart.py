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
    ("VE", "其他費用(業外支出)"),
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
    ("P", "2214", "銷項稅額"), ("P", "2655", "股東往來(負債)"),
    ("T", "3110", "實收資本(股本)"), ("T", "3211", "資本公積"), ("T", "3310-03", "法定盈餘公積"),
    ("T", "3351-03", "累積盈虧"), ("T", "3353", "本期損益"),
    ("UA", "4111-01", "銷貨收入-龍眼鮮果"), ("UA", "4111-02", "銷貨收入-龍眼乾"),
    ("UA", "4111-03", "銷貨收入-龍眼肉"), ("UA", "4111-04", "銷貨收入-龍眼花"),
    ("UA", "4111-05", "銷貨收入-龍眼蜂蜜"),
    ("VA", "5111-01", "採購成本-龍眼鮮果"), ("VA", "5111-02", "採購成本-龍眼乾"),
    ("VA", "5111-03", "採購成本-龍眼肉"), ("VA", "5111-04", "採購成本-龍眼花"),
    ("VA", "5111-05", "採購成本-龍眼蜂蜜"),
    ("VC", "6124", "折舊"),
    ("VD", "6110-01", "薪資費用-薪資"), ("VD", "6110-02", "薪資費用-獎金"), ("VD", "6110-03", "薪資費用-退休金"),
    ("VD", "6112", "文具用品"), ("VD", "6113", "旅費"), ("VD", "6114", "運費"), ("VD", "6115", "郵電費"),
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


def name_of(code, default=None):
    r = q1("SELECT name FROM account WHERE code=?", (code,))
    return r.get("name") or default or code


def accounts(groups=None, active_only=True, postable_only=False):
    """依分組列科目(下拉選單用),回傳 list of dict(code,name,grp)。groups=None 是全部。"""
    where, args = ["1=1"], []
    if groups:
        where.append(f"grp IN ({','.join('?' * len(groups))})"); args += list(groups)
    if active_only:
        where.append("active=1")
    rows = q(f"SELECT code, name, grp, active FROM account WHERE {' AND '.join(where)} ORDER BY code", args)
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
    r = q1("SELECT code, name FROM account WHERE grp='C' AND name=?", (name,))
    if r:
        return (r["code"], r["name"])
    nums = [int(x["code"].split("-")[1]) for x in q("SELECT code FROM account WHERE code LIKE '1103-%'")
            if x["code"].split("-")[1].isdigit()]
    code = f"1103-{(max(nums) if nums else 0) + 1:02d}"
    sort = (q1("SELECT MAX(sort) m FROM account").get("m") or 0) + 1
    execute("INSERT INTO account(code,name,grp,sort) VALUES(?,?,?,?)", (code, name, "C", sort))
    return (code, name)
