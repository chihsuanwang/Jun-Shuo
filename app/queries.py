"""儀表板 / 報表查詢。金額均為 NT$。
season=None 代表全部產季;asof 為『結算日』('latest'=最新訂單日 / 'today' / 'YYYY-MM-DD')。
毛利 = 銷售明細營收 - 明細成本(數量×批次單位成本);運費損益另計。"""
import datetime as dt
from db import q, q1, execute
import ledger


def seasons():
    """所有「有動靜」的產季 —— 只要有訂單或有營運費用就納入(前期只有支出、還沒開賣的產季也會出現)。"""
    rows = q("""
        SELECT season FROM "order"
        UNION
        SELECT CAST(substr(ym, 1, 4) AS INT)
               - (CASE WHEN CAST(substr(ym, 6, 2) AS INT) < 4 THEN 1 ELSE 0 END)
          FROM op_expense
        ORDER BY season""")
    return [r["season"] for r in rows]


def latest_sold_season():
    """最近一個「真的有銷售」的產季(排除只有前期支出、還沒開賣的產季)。無銷售則 None。"""
    rows = q("SELECT DISTINCT season FROM \"order\" WHERE order_kind='銷售' ORDER BY season")
    return rows[-1]["season"] if rows else None


def calendar_years():
    """所有「有動靜」的西曆年——跟 seasons() 同一組資料來源(訂單 + 營運費用),只是
    切法改成 1~12 月而非產季(2026-10 新增,給經營報表的「依西曆年」檢視用)。"""
    rows = q("""SELECT CAST(strftime('%Y', order_date) AS INT) y FROM "order"
                UNION
                SELECT CAST(substr(ym, 1, 4) AS INT) y FROM op_expense
                ORDER BY y""")
    return [r["y"] for r in rows]


def season_of(d):
    """由日期(YYYY-MM-DD 或 date)推產季:4 月初~隔年 3 月底,以起始年命名。
    例:2026-04-01 ~ 2027-03-31 皆屬 2026 產季。"""
    s = d.isoformat() if hasattr(d, "isoformat") else str(d)
    y, m = int(s[:4]), int(s[5:7])
    return y if m >= 4 else y - 1


def as_of(asof=None):
    """結算基準日一律用「今天」(不再提供『最新訂單日』選項);asof 參數保留相容,已忽略。"""
    return dt.date.today().isoformat()


def _season_list(season):
    """把 season 參數正規化成產季 int 清單。
    None / 'all' / '' → 全部有動靜的產季;可傳單一值或 list/tuple/set。"""
    if season in (None, "all", "", 0):
        return seasons()
    if isinstance(season, (list, tuple, set)):
        ss = sorted({int(s) for s in season if str(s).strip() not in ("", "all")})
        return ss or seasons()
    return [int(season)]


def _year_list(year):
    """把 year 參數正規化成西曆年 int 清單,語意同 _season_list,用在 calendar 模式。"""
    if year in (None, "all", "", 0):
        return calendar_years()
    if isinstance(year, (list, tuple, set)):
        ys = sorted({int(y) for y in year if str(y).strip() not in ("", "all")})
        return ys or calendar_years()
    return [int(year)]


def _S(season, alias="o", mode="season", date_col="order_date", year=None):
    """回傳 (clause, params) —— 供 WHERE ... AND {clause} 使用。
    mode='season'(預設,行為不變):season 可為單一產季、產季清單,或 None/'all'(全部),
    依 {alias}.season 欄位過濾。
    mode='calendar'(2026-10 新增,經營報表「依西曆年」檢視用):改依 {alias}.{date_col}
    這個日期欄位落在哪個西曆年過濾(不是比對 season 欄位,因為切法完全不同),
    year 可為單一西曆年、清單,或 None/'all';這個模式下 season 參數被忽略。"""
    if mode == "calendar":
        if year in (None, "all", "", 0):
            return "1=1", []
        if isinstance(year, (list, tuple, set)):
            ys = sorted({int(y) for y in year if str(y).strip() not in ("", "all")})
            if not ys:
                return "1=1", []
            return (f"strftime('%Y',{alias}.{date_col}) IN ({','.join('?' * len(ys))})",
                    [str(y) for y in ys])
        return f"strftime('%Y',{alias}.{date_col})=?", [str(int(year))]
    if season in (None, "all", "", 0):
        return "1=1", []
    if isinstance(season, (list, tuple, set)):
        ss = sorted({int(s) for s in season if str(s).strip() not in ("", "all")})
        if not ss:
            return "1=1", []
        return f"{alias}.season IN ({','.join('?' * len(ss))})", [int(s) for s in ss]
    return f"{alias}.season=?", [int(season)]


# ---------- KPI --------------------------------------------------------
#   「營收」一律用實收(paid_amount,家易標記已收款時填的金額),不是訂單金額 ——
#   他在意的是收支情況,錢真的進來才算數。還沒收到錢的訂單,先不計入營收/毛利,
#   但照樣可以出貨(出貨跟庫存不受付款狀態影響)。
#   ship_payer='客戶付' 的訂單,paid_amount 含運費(代收代付,不是公司的營收),算營收時要扣掉;
#   運費支出(ship_cost)也只算 ship_payer='店家吸收' 的訂單,因為客戶付的運費對公司是淨零。
#   2026-10-08 起售價含稅,家易在訂單上填的銷項稅額(sales_tax)是代收的稅,也要扣掉。
PAID_O = "o.payment_status IN ('已收款','部分收款')"
# 2026-10-16 起跟客人收的運費算收入,所以實收只扣掉銷項稅額(舊資料的「客戶付」運費也一樣當收入看)
REV_PAID = "(o.paid_amount - COALESCE(o.sales_tax,0))"
# 明細層級的未稅營收:每列含稅小計依該單各列佔比分掉銷項稅額(要有 alias ol + o)
LINE_NET = ("(ol.line_subtotal * (1 - COALESCE(o.sales_tax,0) / COALESCE(NULLIF("
            "(SELECT SUM(x.line_subtotal) FROM order_line x WHERE x.order_id=o.order_id),0),1)))")
# 退款 / 退回小農(2026-10-08):管理報表上退款沖減營收,小農退回的成本沖減銷貨成本
RET_REFUND = "CASE WHEN sr.kind='退款' THEN sr.amount ELSE 0 END"
RET_COGS = "CASE WHEN sr.kind='退回小農' THEN sr.amount ELSE 0 END"

def kpi(season=None, asof=None, mode="season", year=None):
    a = as_of(asof)
    sc, sp = _S(season, mode=mode, year=year)
    rev = q1(f"""SELECT COALESCE(SUM({REV_PAID}),0) v
               FROM \"order\" o WHERE {sc} AND order_kind='銷售' AND {PAID_O}""", sp)["v"]
    m = q1(f"""SELECT COALESCE(SUM(COALESCE(ol.line_cost,0)),0) cogs
               FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
               JOIN product p ON p.product_id=ol.product_id
               WHERE {sc} AND o.order_kind='銷售' AND {PAID_O}""", sp)
    ret_gross, ret_cogs = _returns_agg(season, mode=mode, year=year)
    net_rev = rev - ret_gross
    gp = net_rev - (m["cogs"] - ret_cogs)
    oc = q1(f"SELECT COUNT(*) n FROM \"order\" o WHERE {sc} AND order_kind='銷售'", sp)["n"]
    paid_oc = q1(f"SELECT COUNT(*) n FROM \"order\" o WHERE {sc} AND order_kind='銷售' AND {PAID_O}", sp)["n"]
    ship_cost = q1(f"SELECT COALESCE(SUM(shipping_cost_actual),0) v FROM \"order\" o WHERE {sc} AND ship_payer='店家吸收'", sp)["v"]
    ar = q1(f"""SELECT COALESCE(SUM(order_total - COALESCE(paid_amount,0)),0) v, COUNT(*) n FROM "order" o
                WHERE {sc} AND payment_status IN ('待收款','部分收款')""", sp)
    pr = q1(f"""SELECT COALESCE(SUM(COALESCE(ol.line_cost,0)),0) v
                FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                JOIN product p ON p.product_id=ol.product_id
                WHERE {sc} AND o.order_kind<>'銷售'""", sp)["v"]
    return dict(
        revenue=rev, returns=ret_gross, net_revenue=net_rev,
        gross_profit=gp, margin=(gp / net_rev if net_rev else 0),
        orders=oc, aov=(rev / paid_oc if paid_oc else 0),
        ship_cost=ship_cost,
        ar_amount=ar["v"], ar_count=ar["n"],
        pr_cost=pr, pr_ratio=(pr / rev if rev else 0),
        as_of=a,
    )


# ---------- 警示 -----------------------------------------------------
def alerts(season=None, asof=None, mode="season", year=None):
    a = as_of(asof)
    sc, sp = _S(season, mode=mode, year=year)
    out = []
    od = q1(f"""SELECT COUNT(*) n, COALESCE(SUM(order_total),0) v,
                       MAX(julianday(?)-julianday(order_date)) maxdays
                FROM "order" o WHERE {sc} AND payment_status='待收款'
                  AND julianday(?)-julianday(order_date) > 30""", [a] + sp + [a])
    if od["n"]:
        out.append({"text": f"貨款逾期未收 {od['n']} 筆 · NT$ {od['v']:,.0f}(最久 {od['maxdays']:.0f} 天)",
                    "href": "/orders?filter=overdue"})
    ps = q1(f"""SELECT COUNT(*) n FROM "order" o
                WHERE {sc} AND ship_status='待出貨' AND julianday(?)-julianday(order_date) > 3""", sp + [a])
    if ps["n"]:
        out.append({"text": f"超過 3 天還沒出貨 {ps['n']} 筆", "href": "/orders?filter=unshipped"})
    iss = q1("""SELECT COUNT(*) n FROM shipment_issue
                WHERE issue_type IN ('破損','遺失') AND (resolution IS NULL OR resolution='')""")
    if iss["n"]:
        out.append({"text": f"破損 / 遺失待處理 {iss['n']} 件", "href": "/issues"})
    d = q1(f"""SELECT
                 COALESCE(SUM(o.discount_total),0)
                 + COALESCE(SUM(CASE WHEN ol.list_price>ol.unit_price
                                     THEN (ol.list_price-ol.unit_price)*ol.qty ELSE 0 END),0) disc,
                 COALESCE(SUM(COALESCE(NULLIF(ol.list_price,0), ol.unit_price)*ol.qty),0)
                 + COALESCE(SUM(o.discount_total),0) base
               FROM "order" o JOIN order_line ol ON ol.order_id=o.order_id
               WHERE {sc} AND o.order_kind='銷售'""", sp)
    if d["base"] and d["disc"] / d["base"] > 0.03:
        out.append({"text": f"折扣佔比 {d['disc']/d['base']*100:.0f}%(少收的錢)", "href": "/reports"})
    nc = q1(f'SELECT COUNT(*) n FROM "order" o WHERE {sc} AND {NOCOST_O}', sp)["n"]
    if nc:
        out.append({"text": f"{nc} 張銷售訂單還沒填成本移轉價(欠小農沒記到)", "href": "/orders?filter=nocost"})
    rv = q1("SELECT COUNT(*) n FROM review_queue WHERE status='待處理'")["n"]
    if rv:
        out.append({"text": f"待確認資料 {rv} 筆", "href": "/review"})
    return out


# ---------- 儀表板圖表 --------------------------------------------
def monthly(season=None, mode="season", year=None):
    """儀表板「月營收 / 毛利趨勢」。2026-10-23 起跟營收卡片、損益簡表同一套:從總帳抓,營收開單就算、不含稅
    (退款前),毛利 = 營收 − 退款 − 銷貨成本。只列有營收 / 成本 / 退款的月份(淡月不畫 0 點,跟原本一樣)。"""
    rows = []
    for ym in finance_months(season, mode=mode, year=year):
        d = finance_month(ym)
        if round(d["revenue"], 2) or round(d["returns"], 2) or round(d["cogs"], 2):
            rows.append(dict(ym=ym, rev=d["revenue"], gp=d["gross_profit"]))
    return rows


def by_channel(season=None, mode="season", year=None):
    sc, sp = _S(season, mode=mode, year=year)
    rows = q(f"""SELECT c.name,
        (SELECT COUNT(*) FROM "order" o WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') orders,
        (SELECT COALESCE(SUM({LINE_NET}),0) FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
           WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') rev,
        (SELECT COALESCE(SUM(COALESCE(ol.line_cost,0)),0) FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
           JOIN product p ON p.product_id=ol.product_id
           WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') cogs
        FROM channel c""", sp * 3)
    for r in rows:
        r["gp"] = r["rev"] - r["cogs"]
    rows = [r for r in rows if r["orders"]]
    rows.sort(key=lambda r: r["rev"], reverse=True)
    return rows


def ar_aging(season=None, asof=None, mode="season", year=None):
    a = as_of(asof)
    sc, sp = _S(season, mode=mode, year=year)
    rows = q(f"""SELECT o.order_id, o.order_no, o.order_total, o.order_date, o.payment_status,
                        cu.display_name cust,
                        CAST(julianday(?)-julianday(o.order_date) AS INT) days
                 FROM "order" o LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                 WHERE {sc} AND o.payment_status IN ('待收款','部分收款')
                 ORDER BY days DESC""", [a] + sp)
    buckets = {"0–15 天": 0.0, "16–30 天": 0.0, "31 天以上": 0.0}
    for r in rows:
        k = "0–15 天" if r["days"] <= 15 else ("16–30 天" if r["days"] <= 30 else "31 天以上")
        buckets[k] += r["order_total"]
    return buckets, rows


def ar_recently_paid(season=None, limit=15, mode="season", year=None):
    """比照 E應收帳款 sheet 的『已收回的帳款』——最近標記已收款的訂單,依收款日新到舊。"""
    sc, sp = _S(season, mode=mode, year=year)
    rows = q(f"""SELECT o.order_id, o.order_no, o.paid_date, o.paid_amount, o.order_total,
                        cu.display_name cust
                 FROM "order" o LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                 WHERE {sc} AND o.payment_status='已收款' AND o.paid_date IS NOT NULL
                 ORDER BY o.paid_date DESC, o.order_id DESC LIMIT ?""", sp + [limit])
    return rows


CASH_SRC_LABEL = {'op_expense': '營運費用', 'order_sale': '訂單成立',
                   'order_payment': '訂單收款', 'goods_purchase': '進口商品', 'other_income': '其他收益',
                   'equity': '資本異動', 'manual_adjustment': '帳務調整',
                   'asset_acquire': '設備取得', 'sales_return': '退款/退回小農'}

def cash_accounts():
    """現金 / 各銀行帳戶清單——依 ledger_entry 裡實際用過的科目抓,不是另外維護的固定名單。
    2026-09-23 起每家銀行有自己的子代碼(1103-01/02/03…,2026-10-16 起在會計科目表 C 組),用 LIKE
    抓整組,不是只比對『1103』這個舊的共用代碼。"""
    return q("""SELECT DISTINCT account_code, account_name FROM ledger_entry
                WHERE account_code='1102' OR account_code LIKE '1103%'
                ORDER BY account_code, account_name""")


def payment_account_names():
    """「付款/收款帳戶」欄位的自動完成建議清單——2026-09-23 起直接讀 bank_account 主檔
    (銀行帳戶已經有專門的主檔跟固定子代碼,不用再從 ledger_entry 的科目名稱反推)。"""
    import chart
    return [r["name"] for r in chart.bank_accounts()]


def cash_ledger(account_code, account_name):
    """某個現金/銀行帳戶的明細 + 逐筆累計餘額(依日期、entry_id 排序)。"""
    rows = q("""SELECT entry_date, voucher_no, note, source_type, debit, credit
                FROM ledger_entry WHERE account_code=? AND account_name=?
                ORDER BY entry_date, entry_id""", (account_code, account_name))
    bal = 0.0
    for r in rows:
        bal += (r["debit"] or 0) - (r["credit"] or 0)
        r["balance"] = bal
        r["src_label"] = CASH_SRC_LABEL.get(r["source_type"], r["source_type"])
    return rows, bal


def product_mix(season=None, mode="season", year=None):
    sc, sp = _S(season, mode=mode, year=year)
    return q(f"""SELECT p.name, SUM({LINE_NET}) rev, SUM(ol.qty) qty
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 WHERE {sc} AND o.order_kind='銷售' AND ol.is_gift=0
                 GROUP BY p.name ORDER BY rev DESC""", sp)


def new_vs_repeat(season=None, mode="season", year=None):
    sc, sp = _S(season, mode=mode, year=year)
    rows = q(f"""SELECT customer_id, COUNT(*) n, SUM(order_total) v
                 FROM "order" o WHERE {sc} AND order_kind='銷售' GROUP BY customer_id""", sp)
    return dict(
        repeat_rev=sum(r["v"] for r in rows if r["n"] > 1),
        new_rev=sum(r["v"] for r in rows if r["n"] == 1),
        repeat_customers=sum(1 for r in rows if r["n"] > 1),
        total_customers=len(rows),
    )


def top_customers(season=None, n=5, mode="season", year=None):
    sc, sp = _S(season, mode=mode, year=year)
    return q(f"""SELECT cu.display_name, cu.segment, SUM(o.order_total) v
                 FROM "order" o JOIN customer cu ON cu.customer_id=o.customer_id
                 WHERE {sc} AND o.order_kind='銷售'
                 GROUP BY cu.customer_id ORDER BY v DESC LIMIT ?""", sp + [n])


# ---------- 訂單管理 --------------------------------------------
# 銷售訂單有品項的成本移轉價是 0(2026-10-22):多半是 Google 表單 / Excel 匯入的,總帳沒記「欠小農」
NOCOST_O = "o.order_kind='銷售' AND EXISTS (SELECT 1 FROM order_line l WHERE l.order_id=o.order_id AND COALESCE(l.line_cost,0) <= 0)"

def orders_filter(flt="all", kw="", alias="o"):
    """訂單清單 / 匯出共用的篩選條件(對應訂單頁的分頁 + 搜尋框)。
    回傳 (where_clause, params_list);需搭配 LEFT JOIN customer cu 使用。"""
    a = as_of()
    where, args = ["1=1"], []
    if flt == "overdue":
        where.append(f"{alias}.payment_status='待收款' AND julianday(?)-julianday({alias}.order_date) > 30")
        args.append(a)
    elif flt == "unpaid":
        where.append(f"{alias}.payment_status IN ('待收款','部分收款')")
    elif flt == "unshipped":
        where.append(f"{alias}.ship_status='待出貨' AND julianday(?)-julianday({alias}.order_date) > 3")
        args.append(a)
    elif flt == "nocost":
        where.append(NOCOST_O.replace("o.", f"{alias}."))
    if kw:
        where.append(f"({alias}.order_no LIKE ? OR cu.display_name LIKE ?)")
        args += [f"%{kw}%", f"%{kw}%"]
    return " AND ".join(where), args


def orders_list(flt="all", kw=""):
    a = as_of()
    fc, fp = orders_filter(flt, kw)
    sql = f"""SELECT o.order_id, o.order_no, o.order_date, o.order_kind, o.order_total,
                     o.payment_status, o.ship_status, o.invoiced, o.paid_amount,
                     o.shipping_fee_charged, cu.display_name cust,
                     (SELECT c.name FROM channel c WHERE c.channel_id=o.channel_id) chan,
                     CAST(julianday(?)-julianday(o.order_date) AS INT) age
              FROM "order" o LEFT JOIN customer cu ON cu.customer_id=o.customer_id
              WHERE {fc}
              ORDER BY o.order_date DESC, o.order_id DESC LIMIT 400"""
    return q(sql, [a] + fp)


def order_get(oid):
    o = q1("""SELECT o.*, cu.display_name cust,
                     (SELECT name FROM channel WHERE channel_id=o.channel_id) chan
              FROM "order" o LEFT JOIN customer cu ON cu.customer_id=o.customer_id
              WHERE o.order_id=?""", (oid,))
    lines = q("""SELECT ol.*, p.name pname, p.uom, pg.name pg_name
                 FROM order_line ol JOIN product p ON p.product_id=ol.product_id
                 LEFT JOIN product_group pg ON pg.pg_id=p.product_group_id
                 WHERE ol.order_id=? ORDER BY ol.line_id""", (oid,))
    return o, lines


def review_list():
    return q("SELECT * FROM review_queue ORDER BY (status='待處理') DESC, review_id DESC")


def issues_list():
    return q("""SELECT si.*, o.order_no, o.order_date, o.carrier, o.order_id,
                       cu.display_name cust
                FROM shipment_issue si JOIN "order" o ON o.order_id=si.order_id
                LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                ORDER BY (si.resolution IS NULL OR si.resolution='') DESC, si.issue_id DESC""")


# ---------- 報表:獲利 --------------------------------------------
def profit_by_product(season=None, mode="season", year=None):
    sc, sp = _S(season, mode=mode, year=year)
    return q(f"""SELECT p.name, SUM(ol.qty) qty, p.uom,
                        SUM({LINE_NET}) rev,
                        SUM(CASE WHEN ol.list_price>ol.unit_price
                                 THEN (ol.list_price-ol.unit_price)*ol.qty ELSE 0 END) disc,
                        SUM(COALESCE(ol.line_cost,0)) / NULLIF(SUM(ol.qty),0) unit_cost,
                        SUM({LINE_NET} - COALESCE(ol.line_cost,0)) gp,
                        AVG(NULLIF(ol.unit_price,0)) avg_price,
                        AVG(NULLIF(ol.list_price,0)) list_price
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 WHERE {sc} AND o.order_kind='銷售' AND ol.is_gift=0
                 GROUP BY p.name ORDER BY rev DESC""", sp)


# ---------- 報表:跨產季比較(YoY) ---------------------------
def season_compare():
    out = []
    for s in seasons():
        out.append(dict(season=s, **kpi(season=s)))
    # 產季內第 N 月(9月=1 … 3月=7)對齊,方便疊圖
    rows = q(f"""SELECT o.season,
                       ((CAST(strftime('%m', o.order_date) AS INT) - 9 + 12) % 12) + 1 midx,
                       SUM({LINE_NET}) rev
                FROM "order" o JOIN order_line ol ON ol.order_id=o.order_id
                WHERE o.order_kind='銷售'
                GROUP BY o.season, midx ORDER BY o.season, midx""")
    series = {}
    for r in rows:
        series.setdefault(r["season"], {})[r["midx"]] = r["rev"]
    return out, series


# ---------- 財務健康:月損益 ----------------------------------
#   前 4 類多半可歸到某條產品線;後面幾類通常是共同費用。
def opex_cats():
    """財務健康頁的費用項目清單:會計科目表 VD 的名稱(2026-10-16 起項目就是科目;VE 其他支出不算),
    再補上舊資料裡出現過、但已經不在科目表的名稱。"""
    import chart
    names = [r["name"] for r in chart.accounts(["VD"], active_only=False)]
    for r in q("""SELECT DISTINCT category FROM op_expense
                  WHERE account_code IS NULL OR account_code NOT IN (SELECT code FROM account WHERE grp='VE')"""):
        if r["category"] and r["category"] not in names:
            names.append(r["category"])
    return names


OPEX_CATS = ["直接人工", "驗證費", "研發",
             "人事", "場地・倉儲", "行銷", "金流手續費", "設備維護", "培訓", "其他",
             # 9 宮格「其他費用」併進來的子科目(2026-09)
             "其他費用-什項購置", "其他費用-勞務費", "其他費用-設計費", "其他費用-手續費",
             "其他費用-交通費", "其他費用-包裝費", "其他費用-檢驗費", "其他費用-印刷費",
             "其他費用-規費", "其他費用-燃料費",
             # 同事會計科目表 2026-09-23 更新補的獨立費用科目
             "文具用品", "交際費", "捐贈", "伙食費", "職工福利", "佣金支出",
             "進出口費用", "產品保固費用", "利息費用"]

def _month_span(a, b):
    y, m = map(int, a.split("-")); y2, m2 = map(int, b.split("-"))
    out = []
    while (y, m) <= (y2, m2):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m = 1; y += 1
    return out

def _add_months(ym, k):
    y, m = map(int, ym.split("-"))
    m += k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y}-{m:02d}"

def asset_dep_base(a):
    """可提折舊基礎 = 成本 − 補助 − 殘值(不小於 0)。2026-10-16 起補助都是 0;殘值 2026-10-23 起可以填。"""
    return max(0.0, (a["cost"] or 0) - (a["grant_amount"] or 0) - (a["salvage"] or 0))


def asset_life_months(a):
    """使用年限(月):2026-10-16 起存 life_months;舊資料只有 life_years 就 ×12。"""
    return int(a.get("life_months") or 0) or int(a.get("life_years") or 0) * 12


def asset_monthly_dep(a, i):
    """第 i 個月(從 0 算)的折舊:每月 = 無條件進位(基礎 ÷ 月數),最後幾個月不超過剩下的金額
    (1015 版 K 機器設備清冊:每月成本 = ROUNDUP(購買成本 / 使用年限(月)))。"""
    import math
    base, n = asset_dep_base(a), asset_life_months(a)
    if base <= 0 or n <= 0 or i >= n:
        return 0
    per = math.ceil(base / n)
    return max(0, min(per, base - per * i))

def _asset_dep_rows(lo=None, hi=None):
    """每個固定資產的逐月折舊(平均法),攤在耐用年數內;處分後停止。"""
    out = []
    for a in q("SELECT * FROM fixed_asset"):
        months = asset_life_months(a)
        if asset_dep_base(a) <= 0 or months <= 0 or not a["acquire_date"]:
            continue
        start = a["acquire_date"][:7]
        for i in range(months):
            ym = _add_months(start, i)
            if a["disposed_date"] and ym > a["disposed_date"][:7]:
                break
            amt = asset_monthly_dep(a, i)
            if amt and (lo is None or ym >= lo) and (hi is None or ym <= hi):
                out.append(dict(ym=ym, category="折舊",
                                product_group_id=a["product_group_id"], amt=amt))
    return out

def opex_rows(lo=None, hi=None):
    """營運費用逐月列(管理視角):op_expense 依『攤提月數』攤開 + 固定資產逐月折舊。
    amortize_months 空或 <=1 → 當月全額;N → 從 ym 起每月 amount/N,共 N 個月。
    給 lo/hi(YYYY-MM)時只回落在區間內的月份。"""
    out = []
    # 2026-10-17:其他支出(VE 組,利息 / 所得稅)是獨立功能,不算營運費用
    for r in q("""SELECT ym, category, product_group_id, amount, amortize_months FROM op_expense
                  WHERE account_code IS NULL OR account_code NOT IN (SELECT code FROM account WHERE grp='VE')"""):
        n = int(r["amortize_months"]) if r["amortize_months"] and r["amortize_months"] > 1 else 1
        per = (r["amount"] or 0) / n
        for i in range(n):
            ym = _add_months(r["ym"], i)
            if (lo is None or ym >= lo) and (hi is None or ym <= hi):
                out.append(dict(ym=ym, category=r["category"],
                                product_group_id=r["product_group_id"], amt=per))
    out += _asset_dep_rows(lo, hi)
    return out

def months_with_data():
    """有訂單 / 有登錄營運費用 / 有取得固定資產的月份區間;中間空月補上(淡月照樣有支出)。
    上界只到「今天」—— 折舊、攤提排程雖然算到好幾年後,趨勢圖不需要把空白的未來月份畫出來。"""
    marks = [r["ym"] for r in q("SELECT DISTINCT strftime('%Y-%m', order_date) ym FROM \"order\"")]
    marks += [r["ym"] for r in q("SELECT DISTINCT ym FROM op_expense")]
    marks += [r["ym"] for r in
              q("SELECT DISTINCT strftime('%Y-%m', acquire_date) ym FROM fixed_asset WHERE acquire_date IS NOT NULL")]
    marks = [m for m in marks if m]
    today_ym = dt.date.today().strftime("%Y-%m")
    if not marks:
        return [today_ym]
    return _month_span(min(marks), max(max(marks), today_ym))


def finance_months(season=None, mode="season", year=None):
    """月損益頁要顯示的月份:months_with_data() 中屬於指定產季(或 mode='calendar' 時
    指定西曆年)的。season=None / 'all' → 全部;可傳單一產季或產季清單。"""
    mw = months_with_data()
    if mode == "calendar":
        if year in (None, "all", "", 0):
            return mw
        ys = set(_year_list(year))
        return [m for m in mw if int(m[:4]) in ys]
    if season in (None, "all", "", 0):
        return mw
    ss = set(_season_list(season))
    return [m for m in mw if season_of(m + "-01") in ss]

# ---------- 財務健康的數字從總帳抓(2026-10-20,1019 同仁問題第 13 條) ----------
#   營收 = 4 開頭(不含稅、含跟客人收的運費);退款 = 5111-2150-03 原料報廢損失;成本 = 其他 5 開頭;
#   營運費用 = 6 開頭(依科目,含折舊);業外收支 = 7 開頭(不含 7950 所得稅,所以是「稅前」)。
#   都排除年度結轉傳票(不然結轉過的年度會整批變 0)。營收改成「開單就算」(總帳的認列時點),
#   不再是「收到錢才算」。
def _ledger_pnl(lo, hi):
    import ledger as _ledger
    refund_code = _ledger.REFUND_LOSS_ACCOUNT[0]
    rows = q("""SELECT account_code, MAX(account_name) name, SUM(debit - credit) v,
                       COUNT(DISTINCT substr(entry_date,1,7)) n_months
                  FROM ledger_entry
                 WHERE entry_date BETWEEN ? AND ? AND source_type != 'year_closing'
                   AND substr(account_code,1,1) IN ('4','5','6','7')
                 GROUP BY account_code""", (lo, hi))
    rev = refund = cogs = nonop = 0.0
    opex = {}
    for r in rows:
        c, v = r["account_code"], r["v"] or 0
        if c[0] == "4":
            rev -= v
        elif c == refund_code:
            refund += v
        elif c[0] == "5":
            cogs += v
        elif c[0] == "6":
            opex[r["name"]] = opex.get(r["name"], 0) + v
        elif c[0] == "7" and c != "7950":
            nonop -= v
    months = q1("""SELECT COUNT(DISTINCT substr(entry_date,1,7)) n FROM ledger_entry
                    WHERE entry_date BETWEEN ? AND ? AND source_type != 'year_closing'
                      AND substr(account_code,1,1)='6'""", (lo, hi))["n"]
    return dict(revenue=rev, returns=refund, cogs=cogs, opex=opex, nonop=nonop, opex_months=months)

def _pnl_result(d, **extra):
    net_rev = d["revenue"] - d["returns"]
    gp = net_rev - d["cogs"]
    opex_total = sum(d["opex"].values())
    pretax = gp - opex_total + d["nonop"]
    margin = gp / net_rev if net_rev else 0
    out = dict(revenue=d["revenue"], returns=d["returns"], cogs=d["cogs"], gross_profit=gp, margin=margin,
               ship_cost=0.0, nonop=d["nonop"], opex_total=opex_total, opex_months=d["opex_months"], pretax=pretax)
    out.update(extra)
    return out

def _month_end(ym):
    import calendar as _cal
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-{_cal.monthrange(y, m)[1]:02d}"

def finance_month(ym):
    d = _ledger_pnl(f"{ym}-01", _month_end(ym))
    opex = [{"category": k, "amt": v} for k, v in sorted(d["opex"].items(), key=lambda x: -x[1]) if round(v, 2)]
    r = _pnl_result(d, ym=ym, opex=opex)
    r["breakeven"] = (r["opex_total"] / r["margin"]) if r["margin"] else None
    return r

def finance_trend():
    out = []
    for ym in months_with_data():
        d = finance_month(ym)
        out.append(dict(ym=ym, pretax=d["pretax"], revenue=d["revenue"],
                        opex_total=d["opex_total"]))
    return out


# ---------- 固定資產(v2 回合 3) -----------------------------
def _months_between(a, b):
    ay, am = map(int, a.split("-")); by, bm = map(int, b.split("-"))
    return (by - ay) * 12 + (bm - am)

def asset_list(as_of_ym=None):
    """固定資產卡 + 到 as_of_ym 為止的累計折舊、帳面淨值。"""
    if not as_of_ym:
        as_of_ym = as_of()[:7]
    rows = q("""SELECT a.* FROM fixed_asset a
                ORDER BY a.acquire_date DESC, a.asset_id DESC""")
    for a in rows:
        base = asset_dep_base(a)
        months = asset_life_months(a)
        a["dep_base"] = base
        a["life_months_eff"] = months
        a["monthly"] = asset_monthly_dep(a, 0)
        n = 0
        if a["acquire_date"] and a["monthly"]:
            end = as_of_ym
            if a["disposed_date"] and a["disposed_date"][:7] < end:
                end = a["disposed_date"][:7]
            n = min(months, max(0, _months_between(a["acquire_date"][:7], end) + 1))
        a["accum_dep"] = min(base, sum(asset_monthly_dep(a, i) for i in range(n)))
        a["book_value"] = (a["cost"] or 0) - a["accum_dep"]
        a["in_use"] = not a["disposed_date"]
    return rows


def sync_depreciation_vouchers():
    """把總帳的折舊傳票對到目前的固定資產設定(開 /assets 頁、存檔固定資產後呼叫)。
    每台資產從取得月到「這個月」(或處分月)每月該記多少,跟總帳已記的逐月比對:沒記的補上;
    記了但金額不對(成本 / 殘值 / 年限 / 取得日 / 處分日改過)或不該記的(月份移掉了)重記 / 刪掉。
    已結轉的年度一律不動(1019 同仁問題第 6 條)。金額沒變的月份不碰,傳票號碼不會亂跳。"""
    this_ym = dt.date.today().strftime("%Y-%m")
    closed = {int(r["source_id"]) for r in q("SELECT DISTINCT source_id FROM ledger_entry WHERE source_type='year_closing'")}
    for a in q("SELECT * FROM fixed_asset"):
        aid, expected = a["asset_id"], {}
        months = asset_life_months(a)
        if asset_dep_base(a) > 0 and months > 0 and a["acquire_date"]:
            start = a["acquire_date"][:7]
            end = this_ym
            if a["disposed_date"] and a["disposed_date"][:7] < end:
                end = a["disposed_date"][:7]
            for i in range(min(months, max(0, _months_between(start, end) + 1))):
                amt = asset_monthly_dep(a, i)
                if amt:
                    expected[f"{_add_months(start, i)}-01"] = amt
        posted = {r["d"]: r["v"] for r in q("""SELECT entry_date d, SUM(debit) v FROM ledger_entry
                                                WHERE source_type='asset_depreciation' AND source_id=?
                                                GROUP BY entry_date""", (aid,))}
        for d, v in posted.items():
            if int(d[:4]) not in closed and abs(expected.get(d, 0) - (v or 0)) > 0.005:
                execute("DELETE FROM ledger_entry WHERE source_type='asset_depreciation' AND source_id=? AND entry_date=?",
                        (aid, d))
        for d, amt in expected.items():
            if int(d[:4]) not in closed:
                ledger.save_voucher_if_new("asset_depreciation", aid, d, ledger.compose_depreciation_entries(amt),
                                            note=f"折舊:{a['name']}")

# ---------- 退款 / 退回小農(2026-10-08,取代銷貨退回 / 折讓) -------------
def _returns_agg(season, mode="season", year=None):
    """指定產季(可 None / list)的退款 / 退回小農:gross = 退款(沖減營收);
    cogs_back = 小農退回的成本(沖減銷貨成本)。mode='calendar' 時改用 year 參數(西曆年)。"""
    sc, sp = _S(season, alias="sr", mode=mode, date_col="return_date", year=year)
    r = q1(f"""SELECT COALESCE(SUM({RET_REFUND}),0) gross, COALESCE(SUM({RET_COGS}),0) cogs_back
               FROM sales_return sr WHERE {sc}""", sp)
    return r["gross"], r["cogs_back"]

def _returns_month(ym):
    r = q1(f"""SELECT COALESCE(SUM({RET_REFUND}),0) gross, COALESCE(SUM({RET_COGS}),0) cogs_back
               FROM sales_return sr WHERE strftime('%Y-%m', sr.return_date)=?""", (ym,))
    return r["gross"], r["cogs_back"]

def returns_list(order_id=None):
    """退款 / 退回小農清單。給 order_id 只看該單;否則全部(新到舊)。"""
    where = "sr.order_id=?" if order_id else "1=1"
    args = (order_id,) if order_id else ()
    return q(f"""SELECT sr.*, o.order_no, cu.display_name cust
                 FROM sales_return sr
                 JOIN "order" o ON o.order_id=sr.order_id
                 LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                 WHERE {where}
                 ORDER BY sr.return_date DESC, sr.return_id DESC""", args)


def season_finance(season):
    """整個產季合計(從總帳抓,2026-10-20):4 月初~隔年 3 月底;進行中的產季只算到今天。"""
    lo, hi_full = f"{season}-04", f"{season + 1}-03"
    hi = min(_month_end(hi_full), dt.date.today().isoformat())
    d = _ledger_pnl(f"{lo}-01", hi)
    r = _pnl_result(d, season=season, window=f"{lo} ~ {hi_full}")
    r["opex"] = r["opex_total"]
    return r


def calendar_year_finance(year):
    """整個西曆年合計(從總帳抓,2026-10-20):1~12 月;進行中的年度只算到今天。"""
    yr = int(year)
    hi = min(f"{yr}-12-31", dt.date.today().isoformat())
    d = _ledger_pnl(f"{yr}-01-01", hi)
    r = _pnl_result(d, year=yr, window=f"{yr}-01 ~ {yr}-12")
    r["opex"] = r["opex_total"]
    return r


# ---------- 各產品線損益(v2 回合 6) -------------------------
def _season_window(season):
    ss = _season_list(season)
    if not ss:
        return (None, None)
    return f"{ss[0]}-04", f"{ss[-1] + 1}-03"

def _year_window(year):
    """calendar 模式版的 _season_window——回傳格式一樣是 YYYY-MM(跟 opex_rows()/
    purchase_date 比對用,不是完整日期)。"""
    ys = _year_list(year)
    if not ys:
        return (None, None)
    return f"{ys[0]}-01", f"{ys[-1]}-12"

def product_line_pnl(season=None, basis="rev", mode="season", year=None):
    """各產品線損益(管理視角:含攤提 / 折舊,共同費用依 basis 分攤)。
    basis: rev 依營收 / qty 依銷量 / dm 依直接成本。
    直接材料:商品單位成本 × 數量(2026-10-08 起沒有進貨,不再用進貨估算)。
    營收用未稅金額(含稅小計扣掉依佔比分攤的銷項稅額)。"""
    sc, sp = _S(season, mode=mode, year=year)   # alias o
    lo, hi = (_year_window(year) if mode == "calendar" else _season_window(season))

    groups = q("""SELECT g.pg_id, g.name, b.name bu_name
                  FROM product_group g JOIN business_unit b ON b.bu_id=g.bu_id
                  ORDER BY b.sort, g.sort, g.pg_id""")
    L = {g["pg_id"]: dict(pg_id=g["pg_id"], name=g["name"], bu_name=g["bu_name"],
                          revenue=0.0, qty=0.0, dm_batch=0.0,
                          ship_cost=0.0, returns=0.0, returns_cogs=0.0,
                          direct_labor=0.0, field=0.0, cert=0.0, rnd=0.0,
                          dep=0.0, other_line=0.0) for g in groups}

    # 營收 / 銷量 / 直接材料(商品單位成本)
    for r in q(f"""SELECT p.product_group_id pg, ol.qty,
                          {LINE_NET} sub,
                          COALESCE(ol.line_cost,0) bcost
                   FROM order_line ol
                   JOIN "order" o ON o.order_id=ol.order_id
                   JOIN product p ON p.product_id=ol.product_id
                   WHERE {sc} AND o.order_kind='銷售'""", sp):
        g = L.get(r["pg"])
        if not g:
            continue
        g["revenue"] += r["sub"] or 0
        g["qty"] += r["qty"] or 0
        g["dm_batch"] += r["bcost"] or 0

    # 運費(成本)依該單各線營收佔比分攤
    seg = {}
    for r in q(f"""SELECT ol.order_id oid, p.product_group_id pg, ol.line_subtotal sub
                   FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                   JOIN product p ON p.product_id=ol.product_id
                   WHERE {sc} AND o.order_kind='銷售'""", sp):
        seg.setdefault(r["oid"], []).append((r["pg"], r["sub"] or 0))
    for o in q(f"""SELECT order_id oid, shipping_cost_actual ship
                   FROM "order" o WHERE {sc} AND order_kind='銷售' AND ship_payer='店家吸收'""", sp):
        parts = seg.get(o["oid"], [])
        tot = sum(s for _, s in parts) or 1
        for pg, s in parts:
            g = L.get(pg)
            if not g:
                continue
            g["ship_cost"] += (o["ship"] or 0) * s / tot

    # 退款 / 退回小農(2026-10-08):沒有記品項,依原單各線營收佔比分攤。退款沖營收,
    # 小農退回的成本沖直接材料。原單不在這個期間時另外查它的明細來分。
    rc, rp = _S(season, alias="sr", mode=mode, date_col="return_date", year=year)
    for r in q(f"""SELECT sr.order_id oid, sr.kind, sr.amount FROM sales_return sr WHERE {rc}""", rp):
        parts = seg.get(r["oid"])
        if parts is None:
            parts = [(x["pg"], x["sub"] or 0) for x in q(
                """SELECT p.product_group_id pg, ol.line_subtotal sub FROM order_line ol
                   JOIN product p ON p.product_id=ol.product_id WHERE ol.order_id=?""", (r["oid"],))]
        tot = sum(sv for _, sv in parts)
        key = "returns" if r["kind"] == "退款" else "returns_cogs"
        for pg, sv in (parts if tot else []):
            if pg in L:
                L[pg][key] += (r["amount"] or 0) * sv / tot

    # 歸線 / 共同的營運費用 + 折舊(opex_rows 已含攤提 + 折舊)
    shared = 0.0
    cat_key = {"直接人工": "direct_labor", "薪資費用-薪資": "direct_labor", "田間管理": "field",
               "驗證費": "cert", "其他費用-檢驗費": "cert", "研發": "rnd", "研究發展費": "rnd", "折舊": "dep"}
    for r in opex_rows(lo, hi):
        if r["product_group_id"] is None:
            shared += r["amt"]
        elif r["product_group_id"] in L:
            g = L[r["product_group_id"]]
            g[cat_key.get(r["category"], "other_line")] += r["amt"]

    out = list(L.values())
    for g in out:
        g["direct_material"] = g["dm_batch"]
        g["dm_from"] = "商品單位成本" if g["dm_batch"] > 0 else "—"
        g["line_opex"] = (g["direct_labor"] + g["field"] + g["cert"] + g["rnd"]
                          + g["dep"] + g["other_line"])
        g["net_revenue"] = g["revenue"] - g["returns"]
        g["contribution"] = (g["net_revenue"] - (g["direct_material"] - g["returns_cogs"])
                             - g["ship_cost"] - g["line_opex"])

    if basis == "qty":
        w = {g["pg_id"]: max(g["qty"], 0) for g in out}
    elif basis == "dm":
        w = {g["pg_id"]: max(g["direct_material"] + g["direct_labor"], 0) for g in out}
    else:
        w = {g["pg_id"]: max(g["revenue"], 0) for g in out}
    wsum = sum(w.values()) or 1
    for g in out:
        g["shared_alloc"] = shared * w[g["pg_id"]] / wsum
        g["profit"] = g["contribution"] - g["shared_alloc"]
        g["margin"] = g["profit"] / g["net_revenue"] if g["net_revenue"] else 0

    keys = ("revenue", "returns", "net_revenue", "direct_material", "returns_cogs",
            "ship_cost", "direct_labor",
            "field", "cert", "rnd", "dep", "line_opex", "contribution",
            "shared_alloc", "profit")
    total = {k: sum(g[k] for g in out) for k in keys}
    total["name"] = "合計"
    total["margin"] = total["profit"] / total["net_revenue"] if total["net_revenue"] else 0
    return dict(lines=out, total=total, shared_pool=shared, basis=basis,
                window=(f"{lo} ~ {hi}" if lo else "全部"))


def finance_summary(season=None, mode="season", year=None):
    """儀表板用:單一產季(或西曆年)→ season_finance/calendar_year_finance;
    多個 / 全部 → 相加。"""
    if mode == "calendar":
        ys = _year_list(year)
        if not ys:
            return dict(year=None, window="全部年度", revenue=0, returns=0, cogs=0, gross_profit=0,
                        ship_cost=0, nonop=0, opex=0, opex_months=0, pretax=0, margin=0)
        if len(ys) == 1:
            return calendar_year_finance(ys[0])
        parts = [calendar_year_finance(y) for y in ys]
        window = "全部年度" if ys == calendar_years() else "、".join(str(y) for y in ys) + " 年"
        agg = dict(year=None, window=window)
        for f in ("revenue", "returns", "cogs", "gross_profit", "ship_cost", "nonop", "opex", "opex_months", "pretax"):
            agg[f] = sum(p[f] for p in parts)
        net = agg["revenue"] - agg["returns"]
        agg["margin"] = agg["gross_profit"] / net if net else 0
        return agg
    ss = _season_list(season)
    if not ss:
        return dict(season=None, window="全部產季", revenue=0, returns=0, cogs=0, gross_profit=0,
                    ship_cost=0, nonop=0, opex=0, opex_months=0, pretax=0, margin=0)
    if len(ss) == 1:
        return season_finance(ss[0])
    parts = [season_finance(s) for s in ss]
    window = "全部產季" if ss == seasons() else "、".join(str(s) for s in ss) + " 產季"
    agg = dict(season=None, window=window)
    for f in ("revenue", "returns", "cogs", "gross_profit", "ship_cost", "nonop", "opex", "opex_months", "pretax"):
        agg[f] = sum(p[f] for p in parts)
    net = agg["revenue"] - agg["returns"]
    agg["margin"] = agg["gross_profit"] / net if net else 0
    return agg


def cumulative_trend(season=None):
    """累積稅前利潤趨勢:逐月把稅前利潤累加起來。
    負值代表「到這個月為止,前期投入還沒被後面的獲利補回來」;
    線爬回 0 以上,就是這段期間整體轉正。
    season 指定 → 只看該產季的 12 個月(4 月~隔年 3 月)內有資料的月份;None → 全期間跨產季累計。"""
    allm = months_with_data()
    if season not in (None, "all", "", 0):
        s = int(season)
        lo, hi = f"{s}-04", f"{s + 1}-03"
        allm = [m for m in allm if lo <= m <= hi]
    run = 0.0
    out = []
    for ym in allm:
        d = finance_month(ym)
        run += d["pretax"]
        out.append(dict(ym=ym, pretax=round(d["pretax"]), cum=round(run)))
    return out


SELLING_MONTHS = 7  # 產季旺季(有銷售的月):9 月 ~ 隔年 3 月


def sales_target_get(season):
    r = q1("SELECT target_amount FROM sales_target WHERE season=? AND product_id IS NULL", (season,))
    return r.get("target_amount")


def sales_target_set(season, amount):
    from db import execute
    execute("DELETE FROM sales_target WHERE season=? AND product_id IS NULL", (int(season),))
    if amount not in (None, "", 0):
        execute("INSERT INTO sales_target(season, product_id, target_amount) VALUES(?,NULL,?)",
                (int(season), float(amount)))


def season_progress(season=None):
    """產季目標達成 / 依速度預估季末 / 損益兩平線。
    多選產季 → 取其中最新的一季;season=None → 取最新產季。"""
    if isinstance(season, (list, tuple, set)):
        ints = sorted({int(s) for s in season if str(s).strip() not in ("", "all")})
        season = ints[-1] if ints else None
    if season in (None, "all", "", 0):
        # 取「最近一個有銷售的產季」(而非只有前期支出、還沒開賣的那個)
        ss = seasons()
        season = latest_sold_season() or (ss[-1] if ss else None)
    if season is None:
        return None
    season = int(season)
    fin = season_finance(season)
    target = sales_target_get(season)
    nmon = q1("""SELECT COUNT(DISTINCT strftime('%Y-%m', order_date)) n FROM "order"
                 WHERE season=? AND order_kind='銷售'""", (season,))["n"]
    pace = min(nmon, SELLING_MONTHS) / SELLING_MONTHS if nmon else 0
    projected = (fin["revenue"] / pace) if pace else None
    breakeven = (fin["opex"] / fin["margin"]) if fin["margin"] else None
    return dict(
        season=season, window=fin["window"],
        target=target, revenue=fin["revenue"], margin=fin["margin"],
        pct=(fin["revenue"] / target if target else None),
        proj_pct=(projected / target if (target and projected) else None),
        projected=projected, months_done=nmon, selling_months=SELLING_MONTHS,
        breakeven=breakeven, opex=fin["opex"], pretax=fin["pretax"],
        be_pct=(fin["revenue"] / breakeven if breakeven else None),
    )


# ---------- 出貨作業:揀貨單 / 標籤 --------------------------
def shipping_orders(scope="pending", date=None):
    where, args = [], []
    if scope == "date" and date:
        where.append("o.order_date=?"); args.append(date)
    else:
        where.append("o.ship_status='待出貨'")
    where.append("o.order_kind <> '內部領用'")
    orders = q(f"""SELECT o.order_id, o.order_no, o.order_date, o.order_kind,
                          o.ship_method, o.carrier, o.tracking_no, o.ship_status, o.note,
                          cu.display_name cust, cu.phone,
                          (SELECT a.recipient_name FROM address a WHERE a.customer_id=o.customer_id
                             ORDER BY a.is_default DESC LIMIT 1) recipient,
                          (SELECT a.recipient_phone FROM address a WHERE a.customer_id=o.customer_id
                             ORDER BY a.is_default DESC LIMIT 1) rphone,
                          (SELECT a.address_full FROM address a WHERE a.customer_id=o.customer_id
                             ORDER BY a.is_default DESC LIMIT 1) addr
                   FROM "order" o LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                   WHERE {' AND '.join(where)} ORDER BY o.order_date, o.order_no""", args)
    for o in orders:
        o["lines"] = q("""SELECT p.name pname, p.uom, p.type_code_raw spec, ol.qty
                          FROM order_line ol JOIN product p ON p.product_id=ol.product_id
                          WHERE ol.order_id=? ORDER BY ol.line_id""", (o["order_id"],))
    ids = [o["order_id"] for o in orders]
    summary = []
    if ids:
        ph = ",".join("?" * len(ids))
        summary = q(f"""SELECT p.sku, p.name pname, p.uom, SUM(ol.qty) qty,
                               COUNT(DISTINCT ol.order_id) n_orders
                        FROM order_line ol JOIN product p ON p.product_id=ol.product_id
                        WHERE ol.order_id IN ({ph})
                        GROUP BY p.product_id ORDER BY p.sku""", ids)
    return orders, summary


def food_label(product_id, batch_id=None):
    p = q1("SELECT * FROM product WHERE product_id=?", (product_id,))
    if not p:
        return None
    b = q1("SELECT * FROM batch WHERE batch_id=?", (batch_id,)) if batch_id else {}
    mfg = (b or {}).get("mfg_date")
    exp = None
    if mfg and p.get("shelf_life_days"):
        exp = (dt.date.fromisoformat(mfg) + dt.timedelta(days=p["shelf_life_days"])).isoformat()
    return dict(p=p, b=(b or {}), mfg=mfg, exp=exp)


# ---------- 定價試算(管理估算,不進帳本;見 docs/定價分析.md) --------
# 2026-09 改版:鮮果 → 龍眼乾 → 龍眼肉 一路串接成本(上游變,下游自動跟著算),
# 全程以「台斤」計價,轉換率(幾台斤原料做出 1 台斤下一階段)是可調參數,
# 運費用固定金額(不用售價比例),最後再加 5% 營業稅算出可售成本。
# (key, 分組, 標籤, 單位, 預設值, 說明)
PRICING_FIELDS = [
    ("fresh_yield",  "共用",     "全年鮮果總產量",     "台斤/年", 10000,  "建議用近 3 年平均,三品共用同一批鮮果"),
    ("tax_rate",     "共用",     "營業稅率",           "%",       5,      ""),

    ("mat_fert",     "龍眼鮮果", "肥料資材",           "元/年",   6800,   "台肥1號每包340元,估算需求量"),
    ("mat_org",      "龍眼鮮果", "有機基肥",           "元/年",   18000,  "每包60元,估算需求量"),
    ("mat_pest",     "龍眼鮮果", "農藥資材",           "元/年",   1500,   "整年度用量估算"),
    ("lab_weed",     "龍眼鮮果", "人工除草",           "元/年",   24000,  "平均 1 年需要 6 次,每次 2 天"),
    ("lab_harvest",  "龍眼鮮果", "人工採摘",           "元/年",   168000, "平均採收期約 28 天,投入基本人力 3 人"),
    ("lab_spray",    "龍眼鮮果", "噴藥人力",           "元/年",   12000,  "每次噴藥人力 2 人,整年度施藥 3 次"),
    ("fresh_price",  "龍眼鮮果", "目前售價",           "元/台斤", 25,     ""),

    ("ratio_fresh_dry", "龍眼乾", "鮮果轉換率",        "台斤鮮果 / 台斤乾", 3, "例:鮮果 3 台斤 焙成 1 台斤龍眼乾"),
    ("dry_trim",     "龍眼乾",   "① 剪果工費",         "元/台斤", 9,      "加工順序:剪果 → 烘焙 → 後處理"),
    ("dry_roast",    "龍眼乾",   "② 烘焙工費",         "元/台斤", 45,     ""),
    ("dry_post",     "龍眼乾",   "③ 後處理工費",       "元/台斤", 10,     ""),
    ("dry_dep",      "龍眼乾",   "設備攤提",           "元/台斤", 9.6,    "焙灶 / 去殼機等,依設備清冊算"),
    ("dry_pack",     "龍眼乾",   "包材 / 包裝工費",    "元/台斤", 14.4,   ""),
    ("dry_freight",  "龍眼乾",   "運費(固定金額)",     "元/台斤", 12,     "不用售價比例,先抓一個固定值"),
    ("dry_p30",      "龍眼乾",   "#30 售價",           "元/包",   300,    "四種規格加權平均算真實售價"),
    ("dry_q30",      "龍眼乾",   "#30 銷量",           "包/年",   300,    ""),
    ("dry_p28",      "龍眼乾",   "#28 售價",           "元/包",   280,    ""),
    ("dry_q28",      "龍眼乾",   "#28 銷量",           "包/年",   400,    ""),
    ("dry_p26",      "龍眼乾",   "#26 售價",           "元/包",   230,    ""),
    ("dry_q26",      "龍眼乾",   "#26 銷量",           "包/年",   500,    ""),
    ("dry_p24",      "龍眼乾",   "#24 售價",           "元/包",   130,    ""),
    ("dry_q24",      "龍眼乾",   "#24 銷量",           "包/年",   300,    ""),

    ("ratio_dry_meat", "龍眼肉", "龍眼乾轉換率",       "台斤龍眼乾 / 台斤肉", 3, "例:龍眼乾 3 台斤 剝成 1 台斤龍眼肉"),
    ("meat_deshell", "龍眼肉",   "① 剝肉工費",         "元/台斤", 95,     "加工順序:剝肉 → 後處理"),
    ("meat_post",    "龍眼肉",   "② 後處理工費",       "元/台斤", 10,     ""),
    ("meat_pack",    "龍眼肉",   "包材 / 包裝工費",    "元/台斤", 30,     ""),
    ("meat_freight", "龍眼肉",   "運費(固定金額)",     "元/台斤", 25,     "不用售價比例,先抓一個固定值"),
    ("meat_price",   "龍眼肉",   "目前售價",           "元/台斤", 500,    ""),

    ("honey_feed",   "蜂蜜",     "全年蜂群飼養費",     "元/年",   60000,  "糖 / 藥 / 箱材"),
    ("honey_jars",   "蜂蜜",     "全年產罐數",         "罐/年",   300,    "用近 3 年平均,不要用豐收年"),
    ("honey_labor",  "蜂蜜",     "採蜜/搖蜜/濾蜜/裝罐工", "元/罐", 40,     ""),
    ("honey_dep",    "蜂蜜",     "蜂箱 + 搖蜜機折舊",  "元/罐",   11,     ""),
    ("honey_jar",    "蜂蜜",     "玻璃罐(420g)",      "元/罐",   25,     ""),
    ("honey_price",  "蜂蜜",     "目前售價(420g)",    "元/罐",   420,    ""),
]
PRICING_GROUPS = ["共用", "龍眼鮮果", "龍眼乾", "龍眼肉", "蜂蜜"]
PRICING_DEFAULTS = {k: d for k, _g, _l, _u, d, _h in PRICING_FIELDS}


def pricing_params():
    """回傳所有定價參數 {key: value},資料庫沒設的用預設值。"""
    vals = dict(PRICING_DEFAULTS)
    for r in q("SELECT key, value FROM pricing_param"):
        if r["key"] in vals:
            vals[r["key"]] = r["value"]
    return vals


# ---------- 正式三表:試算表 / 資產負債表 / 綜合損益表(2026-09-23) --------
#   同事做帳/報稅用,直接從 ledger_entry 彙總,口徑要跟總帳分毫不差——跟上面
#   season_finance() 那套「給家易看的經營報表」是兩回事,不共用算法。年度一律用西曆年
#   (1~12月)切,跟系統其他地方用的「產季」是兩條不同時間軸。

def statement_years():
    """有總帳資料的西曆年 + 當年(就算今年還沒資料也讓使用者選得到)。"""
    rows = q("SELECT DISTINCT substr(entry_date,1,4) y FROM ledger_entry")
    return sorted({int(r["y"]) for r in rows} | {dt.date.today().year})


def _is_debit_normal(account_code):
    """比照同事的檢核規則分組:1、5、6 開頭跟 7500(含)以後的 7 開頭是「正常餘額在借方」
    (資產/成本/費用類);2、3、4 開頭跟 7500 以前的 7 開頭是「正常餘額在貸方」(負債/權益/
    收入類)。"""
    code_num = account_code.split("-")[0]
    head = code_num[0]
    if head == "7" and code_num.isdigit():
        return int(code_num) >= 7500
    return head in ("1", "5", "6")


def _display_balance(account_code, balance):
    """全站共用的「顯示金額」規則:不教家易 / 同事借貸概念,但數字要有統一道理——
    不管哪種科目,『正常增加的方向』永遠顯示正數。借方正常(資產/成本/費用)的
    debit-credit 本來就是這個方向,原樣顯示;貸方正常(負債/權益/收入)要反過來
    (收入增加、負債增加都該是正數)。跟同事 Excel 範例的呈現方式一致(範例裡金額
    本來就是正數,沒有真的用 -100 代表貸方)。試算表/資產負債表/綜合損益表三張正式
    報表、以後任何要把借貸合併成單一數字顯示的地方,都走這個函式,不要各自手動加
    負號——加出來的正負號不保證跟這條規則一致(2026-09-23 發現的問題就是這樣來的:
    綜合損益表原本不分科目方向、所有列一律加負號,結果費用類反而顯示成負數)。"""
    return balance if _is_debit_normal(account_code) else -balance


PNL_SUMMARY_ACCOUNT = ("3353", "本期損益")  # 同事截圖給的代碼;只有這個沒被 9/23 列為待確認,視為已確認

def trial_balance(year):
    """試算表(2026-10-20 依 1019 同仁問題第 2、3、5 條改):選一個西曆年,每個科目一列——
    期初餘額(前一年底為止累計)、本期借方 / 貸方(這一年的分錄,含年度結轉)、期末餘額 = 期初 + 借 − 貸。
    **正負號:借方是正、貸方是負**(負債、權益、收入的餘額是負數),所以全部科目期末加總 = 0 就是檢核。
    會計科目表上的科目都列(沒有金額也列,顯示 0;不含 3353 / 5950 這種算出來的)。
    「3353 本期損益」這列 = 這一年 4~7 開頭科目的淨額(不含年度結轉傳票,所以結轉前後都看得到當年損益;
    同樣借正貸負,有賺是負數),只顯示、不算進檢核(同事 Excel:要填入數字但加總時不計算他)。"""
    import chart as _chart
    lo, hi = f"{year}-01-01", f"{year}-12-31"
    agg = {r["account_code"]: r for r in q("""
        SELECT account_code, MAX(account_name) account_name,
               SUM(CASE WHEN entry_date < ? THEN debit - credit ELSE 0 END) open_bal,
               SUM(CASE WHEN entry_date BETWEEN ? AND ? THEN debit ELSE 0 END) dr,
               SUM(CASE WHEN entry_date BETWEEN ? AND ? THEN credit ELSE 0 END) cr
          FROM ledger_entry WHERE entry_date <= ?
         GROUP BY account_code""", (lo, lo, hi, lo, hi, hi))}
    rows = []
    for a in _chart.accounts(active_only=False, postable_only=True):
        g = agg.pop(a["code"], None) or {}
        rows.append(dict(account_code=a["code"], account_name=a["name"],
                         open_bal=round(g.get("open_bal") or 0, 2), dr=round(g.get("dr") or 0, 2),
                         cr=round(g.get("cr") or 0, 2), synthetic=False))
    for code, g in agg.items():          # 科目表裡已經刪掉、但總帳還有的(理論上不會有)
        if code == PNL_SUMMARY_ACCOUNT[0]:
            continue
        rows.append(dict(account_code=code, account_name=g["account_name"], open_bal=round(g["open_bal"] or 0, 2),
                         dr=round(g["dr"] or 0, 2), cr=round(g["cr"] or 0, 2), synthetic=False))
    for r in rows:
        r["close_bal"] = round(r["open_bal"] + r["dr"] - r["cr"], 2)
    pnl = q1("""SELECT COALESCE(SUM(debit - credit), 0) v FROM ledger_entry
                WHERE entry_date BETWEEN ? AND ? AND source_type != 'year_closing'
                  AND substr(account_code, 1, 1) IN ('4','5','6','7')""", (lo, hi))["v"]
    code, name = PNL_SUMMARY_ACCOUNT
    rows.append(dict(account_code=code, account_name=name, open_bal=None, dr=None, cr=None,
                     close_bal=round(pnl or 0, 2), synthetic=True))
    rows.sort(key=lambda r: r["account_code"])
    real = [r for r in rows if not r["synthetic"]]
    return dict(year=year, lo=lo, hi=hi, rows=rows,
                total_open=round(sum(r["open_bal"] for r in real), 2),
                total_dr=round(sum(r["dr"] for r in real), 2), total_cr=round(sum(r["cr"] for r in real), 2),
                check=round(sum(r["close_bal"] for r in real), 2), pnl=round(pnl or 0, 2))


def account_ledger(code, lo=None, hi=None):
    """科目明細(2026-10-16,照 1015 版各明細工作表的「產出報表樣式」):某個科目的每一筆分錄,
    依日期排,算累計餘額。累計餘額跟正式報表同一套正負號規則(正常增加的方向是正數)。
    識別碼(發票號碼)跟總帳頁一樣依來源抓。lo/hi 是 YYYY-MM-DD,沒給就全部。"""
    where, args = ["account_code=?"], [code]
    if lo:
        where.append("entry_date>=?"); args.append(lo)
    if hi:
        where.append("entry_date<=?"); args.append(hi)
    opening = 0.0
    if lo:
        r0 = q1("SELECT COALESCE(SUM(debit-credit),0) v FROM ledger_entry WHERE account_code=? AND entry_date<?",
                (code, lo))
        opening = _display_balance(code, r0["v"] or 0)
    rows = q(f"""SELECT * FROM ledger_entry WHERE {' AND '.join(where)}
                 ORDER BY entry_date, voucher_no, entry_id""", args)
    bal = opening
    for r in rows:
        r["amount"] = _display_balance(code, (r["debit"] or 0) - (r["credit"] or 0))
        bal += r["amount"]
        r["balance"] = bal
    return dict(rows=rows, opening=opening, closing=bal)


# ---------- 成本移轉價紀錄(2026-10-23) ---------------------------------
#   家易每張訂單每列自己填成本移轉價(每單位)。這裡把歷史整理出來:新增訂單自動帶入
#   「這個商品上一次的單價」、紀錄頁看每個商品過去設多少。只讀資料,不碰總帳。
#   「上一次」= 訂單日期最新的那一筆(同一天取最後存的,line_id 大的);成本是 0 的不算;
#   不分客戶、不分訂單種類。
def _unit_cost_of(qty, line_cost, unit_cost):
    """每單位成本:存的原值跟這列總額對得上就用原值(2026-10-22 起才有),不然用總額 ÷ 數量。"""
    qty, line_cost = qty or 0, line_cost or 0
    if unit_cost is not None and abs(round(qty * unit_cost, 2) - line_cost) < 0.005:
        return unit_cost
    return round(line_cost / qty, 6) if qty else 0


def cost_records(pid=None):
    """成本 > 0 的訂單明細,依訂單日期由舊到新;每筆附 unit(每單位)、prev(這個商品全部歷史裡的
    前一筆每單位,沒有就 None)、trend(up / down / same / None)。"""
    where, args = ["COALESCE(ol.line_cost,0) > 0", "ol.qty > 0"], []
    if pid:
        where.append("ol.product_id=?"); args.append(int(pid))
    rows = q(f"""SELECT ol.line_id, ol.product_id pid, p.name pname, p.sku, p.uom,
                        ol.qty, ol.line_cost, ol.unit_cost,
                        o.order_id, o.order_no, o.order_date, o.season, cu.display_name cust
                 FROM order_line ol
                 JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                 WHERE {' AND '.join(where)}
                 ORDER BY o.order_date, ol.line_id""", args)
    last = {}
    for r in rows:
        r["unit"] = _unit_cost_of(r["qty"], r["line_cost"], r["unit_cost"])
        p = last.get(r["pid"])
        r["prev"] = p
        r["trend"] = (None if p is None else "same" if abs(r["unit"] - p) < 0.005
                      else "up" if r["unit"] > p else "down")
        last[r["pid"]] = r["unit"]
    return rows


def last_unit_costs(exclude_order=None):
    """{product_id: {u, date, order_no, order_id}}:每個商品上一次的成本移轉價(每單位)。
    exclude_order:訂單明細頁不要拿自己這張當「上一次」。"""
    out = {}
    for r in cost_records():
        if exclude_order and r["order_id"] == exclude_order:
            continue
        out[r["pid"]] = dict(u=r["unit"], date=r["order_date"], order_no=r["order_no"], order_id=r["order_id"])
    return out


def cost_history(season=None, mode="season", year=None, pid=None):
    """成本移轉價紀錄頁:篩選期間內每個商品的摘要 + 選到的商品明細(新到舊)。
    跟前一次比(trend)用全部歷史算,不受篩選期間影響。"""
    if mode == "calendar":
        keep = set(_year_list(year))
        inside = lambda r: int(r["order_date"][:4]) in keep
    else:
        keep = set(_season_list(season))
        inside = lambda r: r["season"] in keep
    rows = [r for r in cost_records() if inside(r)]
    summary = {}
    for r in rows:                         # 舊到新,最後一筆就是最近
        s = summary.setdefault(r["pid"], dict(pid=r["pid"], pname=r["pname"], sku=r["sku"], uom=r["uom"],
                                               n=0, lo=r["unit"], hi=r["unit"]))
        s["n"] += 1
        s["lo"], s["hi"] = min(s["lo"], r["unit"]), max(s["hi"], r["unit"])
        s["last"], s["last_date"], s["trend"] = r["unit"], r["order_date"], r["trend"]
    detail = [r for r in reversed(rows) if pid and r["pid"] == int(pid)]
    return sorted(summary.values(), key=lambda s: s["sku"] or ""), detail
