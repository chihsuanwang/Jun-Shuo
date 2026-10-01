"""儀表板 / 報表查詢。金額均為 NT$。
season=None 代表全部產季;asof 為『結算日』('latest'=最新訂單日 / 'today' / 'YYYY-MM-DD')。
毛利 = 銷售明細營收 - 明細成本(數量×批次單位成本);運費損益另計。"""
import datetime as dt
from db import q, q1
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
PAID_O = "o.payment_status IN ('已收款','部分收款')"

def kpi(season=None, asof=None, mode="season", year=None):
    a = as_of(asof)
    sc, sp = _S(season, mode=mode, year=year)
    rev = q1(f"SELECT COALESCE(SUM(o.paid_amount),0) v FROM \"order\" o WHERE {sc} AND order_kind='銷售' AND {PAID_O}", sp)["v"]
    m = q1(f"""SELECT COALESCE(SUM(ol.qty*COALESCE(p.unit_cost,0)),0) cogs
               FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
               JOIN product p ON p.product_id=ol.product_id
               WHERE {sc} AND o.order_kind='銷售' AND {PAID_O}""", sp)
    ret_gross, ret_cogs = _returns_agg(season, mode=mode, year=year)
    net_rev = rev - ret_gross
    gp = net_rev - (m["cogs"] - ret_cogs)
    oc = q1(f"SELECT COUNT(*) n FROM \"order\" o WHERE {sc} AND order_kind='銷售'", sp)["n"]
    paid_oc = q1(f"SELECT COUNT(*) n FROM \"order\" o WHERE {sc} AND order_kind='銷售' AND {PAID_O}", sp)["n"]
    ship_cost = q1(f"SELECT COALESCE(SUM(shipping_cost_actual),0) v FROM \"order\" o WHERE {sc}", sp)["v"]
    ar = q1(f"""SELECT COALESCE(SUM(order_total - COALESCE(paid_amount,0)),0) v, COUNT(*) n FROM "order" o
                WHERE {sc} AND payment_status IN ('待收款','部分收款')""", sp)
    pr = q1(f"""SELECT COALESCE(SUM(ol.qty*COALESCE(p.unit_cost,0)),0) v
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
    rv = q1("SELECT COUNT(*) n FROM review_queue WHERE status='待處理'")["n"]
    if rv:
        out.append({"text": f"待確認資料 {rv} 筆", "href": "/review"})
    try:
        neg, low = stock_low_count()
        if neg:
            out.append({"text": f"庫存為負 {neg} 個品項(超賣)", "href": "/stock"})
        if low:
            out.append({"text": f"庫存偏低 {low} 個品項", "href": "/stock"})
    except Exception:
        pass
    return out


# ---------- 儀表板圖表 --------------------------------------------
def monthly(season=None, mode="season", year=None):
    """依訂單日期歸月,金額用實收(只算已收款 / 部分收款的訂單)。"""
    sc, sp = _S(season, mode=mode, year=year)
    rev_rows = q(f"""SELECT strftime('%Y-%m', o.order_date) ym, COALESCE(SUM(o.paid_amount),0) rev
                     FROM "order" o WHERE {sc} AND o.order_kind='銷售' AND {PAID_O}
                     GROUP BY ym""", sp)
    cogs_rows = q(f"""SELECT strftime('%Y-%m', o.order_date) ym,
                             COALESCE(SUM(ol.qty*COALESCE(p.unit_cost,0)),0) cogs
                      FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                      JOIN product p ON p.product_id=ol.product_id
                      WHERE {sc} AND o.order_kind='銷售' AND {PAID_O}
                      GROUP BY ym""", sp)
    cogs_map = {r["ym"]: r["cogs"] for r in cogs_rows}
    rows = [dict(ym=r["ym"], rev=r["rev"], gp=r["rev"] - cogs_map.get(r["ym"], 0)) for r in rev_rows]
    rc, rp = _S(season, alias="sr", mode=mode, date_col="return_date", year=year)
    rmap = {r["ym"]: r for r in q(f"""SELECT strftime('%Y-%m', sr.return_date) ym,
                COALESCE(SUM(sr.amount),0) ret,
                COALESCE(SUM(CASE WHEN sr.restock=1
                     THEN COALESCE(sr.qty,0)*COALESCE(p.unit_cost,0) ELSE 0 END),0) rcogs
              FROM sales_return sr LEFT JOIN product p ON p.product_id=sr.product_id
              WHERE {rc} GROUP BY ym""", rp)}
    by_ym = {r["ym"]: r for r in rows}
    for ym, x in rmap.items():
        r = by_ym.get(ym)
        if not r:
            r = dict(ym=ym, rev=0.0, gp=0.0)
            rows.append(r)
        r["rev"] = (r["rev"] or 0) - x["ret"]
        r["gp"] = (r["gp"] or 0) - x["ret"] + x["rcogs"]
    rows.sort(key=lambda r: r["ym"])
    return rows


def by_channel(season=None, mode="season", year=None):
    sc, sp = _S(season, mode=mode, year=year)
    rows = q(f"""SELECT c.name,
        (SELECT COUNT(*) FROM "order" o WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') orders,
        (SELECT COALESCE(SUM(ol.line_subtotal),0) FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
           WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') rev,
        (SELECT COALESCE(SUM(ol.qty*COALESCE(p.unit_cost,0)),0) FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
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


CASH_SRC_LABEL = {'purchase': '進貨', 'op_expense': '營運費用', 'order_sale': '訂單成立',
                   'order_payment': '訂單收款', 'production_in': '生產入庫', 'other_income': '其他收益',
                   'equity': '資本異動', 'manual_adjustment': '帳務調整'}

def cash_accounts():
    """現金 / 各銀行帳戶清單——依 ledger_entry 裡實際用過的科目抓,不是另外維護的固定名單。
    2026-09-23 起每家銀行有自己的子代碼(1103-01/02/03…,見 bank_account 主檔),用 LIKE
    抓整組,不是只比對『1103』這個舊的共用代碼。"""
    return q("""SELECT DISTINCT account_code, account_name FROM ledger_entry
                WHERE account_code='1102' OR account_code LIKE '1103%'
                ORDER BY account_code, account_name""")


def payment_account_names():
    """「付款/收款帳戶」欄位的自動完成建議清單——2026-09-23 起直接讀 bank_account 主檔
    (銀行帳戶已經有專門的主檔跟固定子代碼,不用再從 ledger_entry 的科目名稱反推)。"""
    return [r["name"] for r in q("SELECT name FROM bank_account ORDER BY name")]


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


def stock_value_rows(lo, hi):
    """存貨管理(含金額)——依商品列期初/入庫/出庫/期末的數量+金額。單位成本用商品目錄的
    unit_cost 概算(不分批次,跟現在毛利/COGS 計算同一套邏輯)。lo/hi 是 YYYY-MM-DD。"""
    prods = q("SELECT product_id, sku, name, uom, unit_cost FROM product ORDER BY status, sku")
    out = []
    for p in prods:
        pid = p["product_id"]
        begin = q1("SELECT COALESCE(SUM(qty),0) v FROM stock_move WHERE product_id=? AND move_date<?",
                   (pid, lo))["v"]
        inn = q1("""SELECT COALESCE(SUM(qty),0) v FROM stock_move
                    WHERE product_id=? AND move_date BETWEEN ? AND ? AND qty>0""", (pid, lo, hi))["v"]
        outq = q1("""SELECT COALESCE(SUM(qty),0) v FROM stock_move
                     WHERE product_id=? AND move_date BETWEEN ? AND ? AND qty<0""", (pid, lo, hi))["v"]
        end = begin + inn + outq
        if begin == 0 and inn == 0 and outq == 0:
            continue
        uc = p["unit_cost"] or 0
        out.append(dict(sku=p["sku"], name=p["name"], uom=p["uom"], unit_cost=uc,
                         begin_qty=begin, begin_amt=begin * uc,
                         in_qty=inn, in_amt=inn * uc,
                         out_qty=-outq, out_amt=-outq * uc,
                         end_qty=end, end_amt=end * uc))
    return out


def product_mix(season=None, mode="season", year=None):
    sc, sp = _S(season, mode=mode, year=year)
    return q(f"""SELECT p.name, SUM(ol.line_subtotal) rev, SUM(ol.qty) qty
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
    if kw:
        where.append(f"({alias}.order_no LIKE ? OR cu.display_name LIKE ?)")
        args += [f"%{kw}%", f"%{kw}%"]
    return " AND ".join(where), args


def orders_list(flt="all", kw=""):
    a = as_of()
    fc, fp = orders_filter(flt, kw)
    sql = f"""SELECT o.order_id, o.order_no, o.order_date, o.order_kind, o.order_total,
                     o.payment_status, o.ship_status, o.invoiced, o.paid_amount,
                     o.shipping_cost_actual, o.ship_payer, cu.display_name cust,
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
    lines = q("""SELECT ol.*, p.name pname, p.uom, p.unit_cost, pg.name pg_name
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
                        SUM(ol.line_subtotal) rev,
                        SUM(CASE WHEN ol.list_price>ol.unit_price
                                 THEN (ol.list_price-ol.unit_price)*ol.qty ELSE 0 END) disc,
                        AVG(NULLIF(p.unit_cost,0)) unit_cost,
                        SUM(ol.line_subtotal - ol.qty*COALESCE(p.unit_cost,0)) gp,
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
    rows = q("""SELECT o.season,
                       ((CAST(strftime('%m', o.order_date) AS INT) - 9 + 12) % 12) + 1 midx,
                       SUM(ol.line_subtotal) rev
                FROM "order" o JOIN order_line ol ON ol.order_id=o.order_id
                WHERE o.order_kind='銷售'
                GROUP BY o.season, midx ORDER BY o.season, midx""")
    series = {}
    for r in rows:
        series.setdefault(r["season"], {})[r["midx"]] = r["rev"]
    return out, series


# ---------- 財務健康:月損益 ----------------------------------
#   前 4 類多半可歸到某條產品線;後面幾類通常是共同費用。
OPEX_CATS = ["直接人工", "田間管理", "驗證費", "研發",
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
    """可提折舊基礎 = 成本 − 補助 − 殘值(不小於 0)。"""
    return max(0.0, (a["cost"] or 0) - (a["grant_amount"] or 0) - (a["salvage"] or 0))

def _asset_dep_rows(lo=None, hi=None):
    """每個固定資產的逐月折舊(平均法),攤在耐用年數內;處分後停止。"""
    out = []
    for a in q("SELECT * FROM fixed_asset"):
        base = asset_dep_base(a)
        life = a["life_years"] or 0
        if base <= 0 or life <= 0 or not a["acquire_date"]:
            continue
        monthly = base / life / 12
        start = a["acquire_date"][:7]
        for i in range(life * 12):
            ym = _add_months(start, i)
            if a["disposed_date"] and ym > a["disposed_date"][:7]:
                break
            if (lo is None or ym >= lo) and (hi is None or ym <= hi):
                out.append(dict(ym=ym, category="折舊",
                                product_group_id=a["product_group_id"], amt=monthly))
    return out

def opex_rows(lo=None, hi=None):
    """營運費用逐月列(管理視角):op_expense 依『攤提月數』攤開 + 固定資產逐月折舊。
    amortize_months 空或 <=1 → 當月全額;N → 從 ym 起每月 amount/N,共 N 個月。
    給 lo/hi(YYYY-MM)時只回落在區間內的月份。"""
    out = []
    for r in q("SELECT ym, category, product_group_id, amount, amortize_months FROM op_expense"):
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

def finance_month(ym):
    rev = q1(f"""SELECT COALESCE(SUM(paid_amount),0) v FROM "order" o
                WHERE order_kind='銷售' AND {PAID_O} AND strftime('%Y-%m',order_date)=?""", (ym,))["v"]
    cogs = q1(f"""SELECT COALESCE(SUM(ol.qty*COALESCE(p.unit_cost,0)),0) v
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 WHERE o.order_kind='銷售' AND {PAID_O} AND strftime('%Y-%m',o.order_date)=?""", (ym,))["v"]
    ship_cost = q1("""SELECT COALESCE(SUM(shipping_cost_actual),0) v
                       FROM "order" WHERE strftime('%Y-%m',order_date)=?""", (ym,))["v"]
    by_cat = {}
    for r in opex_rows(ym, ym):
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + r["amt"]
    opex = [{"category": k, "amt": v} for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]
    opex_total = sum(by_cat.values())
    ret_gross, ret_cogs = _returns_month(ym)
    cogs_net = cogs - ret_cogs
    net_rev = rev - ret_gross
    gp = net_rev - cogs_net
    margin = gp / net_rev if net_rev else 0
    pretax = gp - ship_cost - opex_total
    breakeven = (opex_total / margin) if margin else None
    return dict(ym=ym, revenue=rev, returns=ret_gross, cogs=cogs_net, gross_profit=gp, margin=margin,
                ship_cost=ship_cost, opex=opex, opex_total=opex_total,
                pretax=pretax, breakeven=breakeven)

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
    rows = q("""SELECT a.*,
                       (SELECT COUNT(*) FROM purchase p WHERE p.purchase_id=a.source_purchase_id) from_buy
                FROM fixed_asset a
                ORDER BY a.acquire_date DESC, a.asset_id DESC""")
    for a in rows:
        base = asset_dep_base(a)
        life = a["life_years"] or 0
        a["dep_base"] = base
        a["monthly"] = (base / life / 12) if (base > 0 and life) else 0
        n = 0
        if a["acquire_date"] and a["monthly"]:
            end = as_of_ym
            if a["disposed_date"] and a["disposed_date"][:7] < end:
                end = a["disposed_date"][:7]
            n = min(life * 12, max(0, _months_between(a["acquire_date"][:7], end) + 1))
        a["accum_dep"] = min(base, a["monthly"] * n)
        a["book_value"] = (a["cost"] or 0) - a["accum_dep"]
        a["in_use"] = not a["disposed_date"]
    return rows


def sync_depreciation_vouchers():
    """開 /assets 頁時呼叫:把每個在用資產從取得月到「這個月」(或處分月)還沒記過的折舊
    月份補上分錄。已經記過的月份不動——用 ledger.save_voucher_if_new,不是整張重開。"""
    this_ym = dt.date.today().strftime("%Y-%m")
    for a in q("SELECT * FROM fixed_asset"):
        base = asset_dep_base(a)
        life = a["life_years"] or 0
        if base <= 0 or life <= 0 or not a["acquire_date"]:
            continue
        monthly = base / life / 12
        start = a["acquire_date"][:7]
        end = this_ym
        if a["disposed_date"] and a["disposed_date"][:7] < end:
            end = a["disposed_date"][:7]
        n = min(life * 12, max(0, _months_between(start, end) + 1))
        legs = ledger.compose_depreciation_entries(monthly)
        for i in range(n):
            ym = _add_months(start, i)
            ledger.save_voucher_if_new("asset_depreciation", a["asset_id"], f"{ym}-01", legs, "D",
                                        note=f"折舊:{a['name']}")

# ---------- 銷貨退回 / 折讓(v2 回合 8) ----------------------
def _returns_agg(season, mode="season", year=None):
    """指定產季(可 None / list)的退回 / 折讓:gross = 沖減營收;cogs_back = restock 回沖的成本。
    mode='calendar' 時改用 year 參數(西曆年)。"""
    sc, sp = _S(season, alias="sr", mode=mode, date_col="return_date", year=year)
    r = q1(f"""SELECT COALESCE(SUM(sr.amount),0) gross,
                      COALESCE(SUM(CASE WHEN sr.restock=1
                           THEN COALESCE(sr.qty,0)*COALESCE(p.unit_cost,0) ELSE 0 END),0) cogs_back
               FROM sales_return sr LEFT JOIN product p ON p.product_id=sr.product_id
               WHERE {sc}""", sp)
    return r["gross"], r["cogs_back"]

def _returns_month(ym):
    r = q1("""SELECT COALESCE(SUM(sr.amount),0) gross,
                     COALESCE(SUM(CASE WHEN sr.restock=1
                          THEN COALESCE(sr.qty,0)*COALESCE(p.unit_cost,0) ELSE 0 END),0) cogs_back
              FROM sales_return sr LEFT JOIN product p ON p.product_id=sr.product_id
              WHERE strftime('%Y-%m', sr.return_date)=?""", (ym,))
    return r["gross"], r["cogs_back"]

def returns_list(order_id=None):
    """退貨 / 折讓清單。給 order_id 只看該單;否則全部(新到舊)。"""
    where = "sr.order_id=?" if order_id else "1=1"
    args = (order_id,) if order_id else ()
    return q(f"""SELECT sr.*, o.order_no, cu.display_name cust,
                        p.name pname, p.uom
                 FROM sales_return sr
                 JOIN "order" o ON o.order_id=sr.order_id
                 LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                 LEFT JOIN product p ON p.product_id=sr.product_id
                 WHERE {where}
                 ORDER BY sr.return_date DESC, sr.return_id DESC""", args)


def season_finance(season):
    """整個產季合計:銷售月的營收 − 已經過去月份的營運費用。
    產季全長是 4 月初~隔年 3 月底共 12 個月,但還沒發生的月份不預先算進費用裡
    (進行中的產季只算「已經過去的月份」,不會因為費用先算滿 12 個月而顯得稅前利潤一大包負的)。
    銷貨退回 / 折讓依「發生產季」沖減(cogs 為回沖後淨額)。"""
    rev = q1(f"""SELECT COALESCE(SUM(paid_amount),0) v FROM "order" o
                WHERE season=? AND order_kind='銷售' AND {PAID_O}""", (season,))["v"]
    cogs = q1(f"""SELECT COALESCE(SUM(ol.qty*COALESCE(p.unit_cost,0)),0) v
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 WHERE o.season=? AND o.order_kind='銷售' AND {PAID_O}""", (season,))["v"]
    ship_cost = q1("""SELECT COALESCE(SUM(shipping_cost_actual),0) v
                       FROM "order" WHERE season=?""", (season,))["v"]
    ret_gross, ret_cogs = _returns_agg(season)
    cogs_net = cogs - ret_cogs
    net_rev = rev - ret_gross
    lo, hi_full = f"{season}-04", f"{season + 1}-03"
    hi = min(hi_full, dt.date.today().strftime("%Y-%m"))  # 進行中的產季只算到這個月,不預先算未發生的費用
    ox_rows = opex_rows(lo, hi)
    opex_v = sum(r["amt"] for r in ox_rows)
    opex_n = len(set(r["ym"] for r in ox_rows))
    gp = net_rev - cogs_net
    return dict(season=season, window=f"{lo} ~ {hi_full}",
                revenue=rev, returns=ret_gross, cogs=cogs_net,
                gross_profit=gp, ship_cost=ship_cost,
                opex=opex_v, opex_months=opex_n,
                pretax=gp - ship_cost - opex_v,
                margin=(gp / net_rev if net_rev else 0))


def calendar_year_finance(year):
    """整個西曆年合計(2026-10 新增)——結構完全比照 season_finance(),只是窗口改成
    1~12 月,給經營報表「依西曆年」檢視用。回傳 dict 用 year= 取代 season= 當 key
    (模板沒有地方直接讀 fin.season,已確認安全)。"""
    yr = str(int(year))
    rev = q1(f"""SELECT COALESCE(SUM(paid_amount),0) v FROM "order" o
                WHERE strftime('%Y',order_date)=? AND order_kind='銷售' AND {PAID_O}""", (yr,))["v"]
    cogs = q1(f"""SELECT COALESCE(SUM(ol.qty*COALESCE(p.unit_cost,0)),0) v
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 WHERE strftime('%Y',o.order_date)=? AND o.order_kind='銷售' AND {PAID_O}""", (yr,))["v"]
    ship_cost = q1("""SELECT COALESCE(SUM(shipping_cost_actual),0) v
                       FROM "order" WHERE strftime('%Y',order_date)=?""", (yr,))["v"]
    ret_gross, ret_cogs = _returns_agg(None, mode="calendar", year=int(year))
    cogs_net = cogs - ret_cogs
    net_rev = rev - ret_gross
    lo, hi_full = f"{yr}-01", f"{yr}-12"
    hi = min(hi_full, dt.date.today().strftime("%Y-%m"))  # 進行中的年度只算到這個月
    ox_rows = opex_rows(lo, hi)
    opex_v = sum(r["amt"] for r in ox_rows)
    opex_n = len(set(r["ym"] for r in ox_rows))
    gp = net_rev - cogs_net
    return dict(year=int(year), window=f"{lo} ~ {hi_full}",
                revenue=rev, returns=ret_gross, cogs=cogs_net,
                gross_profit=gp, ship_cost=ship_cost,
                opex=opex_v, opex_months=opex_n,
                pretax=gp - ship_cost - opex_v,
                margin=(gp / net_rev if net_rev else 0))


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
    直接材料:有批次成本用批次,否則用歸該線的進貨(原料 / 包材 / 委外)。"""
    sc, sp = _S(season, mode=mode, year=year)   # alias o
    lo, hi = (_year_window(year) if mode == "calendar" else _season_window(season))

    groups = q("""SELECT g.pg_id, g.name, b.name bu_name
                  FROM product_group g JOIN business_unit b ON b.bu_id=g.bu_id
                  ORDER BY b.sort, g.sort, g.pg_id""")
    L = {g["pg_id"]: dict(pg_id=g["pg_id"], name=g["name"], bu_name=g["bu_name"],
                          revenue=0.0, qty=0.0, dm_batch=0.0, dm_purchase=0.0,
                          ship_cost=0.0, returns=0.0, returns_cogs=0.0,
                          direct_labor=0.0, field=0.0, cert=0.0, rnd=0.0,
                          dep=0.0, other_line=0.0) for g in groups}

    # 營收 / 銷量 / 直接材料(商品單位成本)
    for r in q(f"""SELECT p.product_group_id pg, ol.qty,
                          ol.line_subtotal sub,
                          ol.qty * COALESCE(p.unit_cost,0) bcost
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
                   FROM "order" o WHERE {sc} AND order_kind='銷售'""", sp):
        parts = seg.get(o["oid"], [])
        tot = sum(s for _, s in parts) or 1
        for pg, s in parts:
            g = L.get(pg)
            if not g:
                continue
            g["ship_cost"] += (o["ship"] or 0) * s / tot

    # 銷貨退回 / 折讓:有 product_id → 歸該線;純折讓(無品項)→ 依原單各線營收佔比分攤(僅金額)
    rc, rp = _S(season, alias="sr", mode=mode, date_col="return_date", year=year)
    for r in q(f"""SELECT sr.order_id oid, sr.amount, sr.qty, sr.restock,
                          COALESCE(p.unit_cost,0) ucost, p.product_group_id pg
                   FROM sales_return sr
                   LEFT JOIN product p ON p.product_id=sr.product_id
                   WHERE {rc}""", rp):
        amt = r["amount"] or 0
        back = (r["qty"] or 0) * r["ucost"] if r["restock"] else 0
        if r["pg"] in L:
            L[r["pg"]]["returns"] += amt
            L[r["pg"]]["returns_cogs"] += back
        else:
            parts = seg.get(r["oid"], [])
            tot = sum(s for _, s in parts) or 0
            for pg, s in (parts if tot else []):
                if pg in L:
                    L[pg]["returns"] += amt * s / tot

    # 無批次成本的線 → 用歸該線的進貨(原料 / 包材 / 委外,非固定資產)
    for r in q("""SELECT product_group_id pg, COALESCE(SUM(amount),0) v
                  FROM purchase
                  WHERE is_fixed_asset=0 AND category IN ('原料','包材','委外加工')
                    AND product_group_id IS NOT NULL
                    AND (? IS NULL OR substr(purchase_date,1,7) >= ?)
                    AND (? IS NULL OR substr(purchase_date,1,7) <= ?)
                  GROUP BY product_group_id""", (lo, lo, hi, hi)):
        if r["pg"] in L:
            L[r["pg"]]["dm_purchase"] += r["v"]

    # 歸線 / 共同的營運費用 + 折舊(opex_rows 已含攤提 + 折舊)
    shared = 0.0
    cat_key = {"直接人工": "direct_labor", "田間管理": "field", "驗證費": "cert",
               "研發": "rnd", "折舊": "dep"}
    for r in opex_rows(lo, hi):
        if r["product_group_id"] is None:
            shared += r["amt"]
        elif r["product_group_id"] in L:
            g = L[r["product_group_id"]]
            g[cat_key.get(r["category"], "other_line")] += r["amt"]

    out = list(L.values())
    for g in out:
        g["direct_material"] = g["dm_batch"] if g["dm_batch"] > 0 else g["dm_purchase"]
        g["dm_from"] = "商品單位成本" if g["dm_batch"] > 0 else ("進貨估算" if g["dm_purchase"] else "—")
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
                        ship_cost=0, opex=0, opex_months=0, pretax=0, margin=0)
        if len(ys) == 1:
            return calendar_year_finance(ys[0])
        parts = [calendar_year_finance(y) for y in ys]
        window = "全部年度" if ys == calendar_years() else "、".join(str(y) for y in ys) + " 年"
        agg = dict(year=None, window=window)
        for f in ("revenue", "returns", "cogs", "gross_profit", "ship_cost", "opex", "opex_months", "pretax"):
            agg[f] = sum(p[f] for p in parts)
        net = agg["revenue"] - agg["returns"]
        agg["margin"] = agg["gross_profit"] / net if net else 0
        return agg
    ss = _season_list(season)
    if not ss:
        return dict(season=None, window="全部產季", revenue=0, returns=0, cogs=0, gross_profit=0,
                    ship_cost=0, opex=0, opex_months=0, pretax=0, margin=0)
    if len(ss) == 1:
        return season_finance(ss[0])
    parts = [season_finance(s) for s in ss]
    window = "全部產季" if ss == seasons() else "、".join(str(s) for s in ss) + " 產季"
    agg = dict(season=None, window=window)
    for f in ("revenue", "returns", "cogs", "gross_profit", "ship_cost", "opex", "opex_months", "pretax"):
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


# ---------- 庫存(SKU 層) -----------------------------------
STOCK_TYPES_IN  = ["期初庫存", "分裝入庫", "退貨入庫"]
STOCK_TYPES_ADJ = ["盤點調整", "損耗報廢"]

def stock_on_hand():
    rows = q("""SELECT p.product_id, p.sku, p.name, p.uom, p.status, p.low_stock,
                       COALESCE((SELECT SUM(sm.qty) FROM stock_move sm
                                 WHERE sm.product_id=p.product_id),0) on_hand
                FROM product p ORDER BY p.status, p.sku""")
    for r in rows:
        thr = r["low_stock"] if r["low_stock"] is not None else 0
        r["low"] = r["on_hand"] <= thr
    return rows


def stock_on_hand_map():
    return {r["product_id"]: r["on_hand"]
            for r in q("""SELECT p.product_id,
                            COALESCE((SELECT SUM(sm.qty) FROM stock_move sm
                                      WHERE sm.product_id=p.product_id),0) on_hand
                          FROM product p""")}

def stock_moves(product_id=None, mtype=None, limit=400):
    where, args = ["1=1"], []
    if product_id:
        where.append("sm.product_id=?"); args.append(int(product_id))
    if mtype:
        where.append("sm.move_type=?"); args.append(mtype)
    return q(f"""SELECT sm.*, p.sku, p.name pname, p.uom, o.order_no
                 FROM stock_move sm
                 JOIN product p ON p.product_id=sm.product_id
                 LEFT JOIN "order" o ON o.order_id=sm.ref_order_id
                 WHERE {' AND '.join(where)}
                 ORDER BY sm.move_date DESC, sm.move_id DESC LIMIT ?""", args + [limit])

def stock_low_count():
    rows = stock_on_hand()
    neg = sum(1 for r in rows if r["on_hand"] < 0)
    low = sum(1 for r in rows if 0 <= r["on_hand"] and r["low"] and r["status"] == "在售")
    return neg, low


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


def trial_balance(as_of):
    """試算表:每個用過的科目,累計到 as_of(含)為止的借貸合計 = 期末餘額。含已經貼過的
    年度結轉傳票——結轉會把已結束年度的 4~7 開頭科目沖平、餘額留到累積盈虧,試算表才會對。
    順便算一個檢核值(比照同事的檢核規則,理論上永遠是 0,因為每張傳票本來就借貸相等,
    顯示出來是給同事一個熟悉的核對數字)。"""
    rows = q("""SELECT account_code, account_name, SUM(debit) db, SUM(credit) cr
                FROM ledger_entry WHERE entry_date<=?
                GROUP BY account_code, account_name
                HAVING SUM(debit)<>0 OR SUM(credit)<>0
                ORDER BY account_code""", (as_of,))
    for r in rows:
        r["balance"] = (r["db"] or 0) - (r["cr"] or 0)
        r["disp"] = _display_balance(r["account_code"], r["balance"])
    debit_side  = sum(r["disp"] for r in rows if _is_debit_normal(r["account_code"]))
    credit_side = sum(r["disp"] for r in rows if not _is_debit_normal(r["account_code"]))
    return dict(as_of=as_of, rows=rows, debit_side=debit_side, credit_side=credit_side,
                check=round(debit_side - credit_side, 2))


def income_statement(year):
    """綜合損益表:只抓當年度(西曆年,進行中的年份只算到今天為止,比照 season_finance()
    的做法)、代碼開頭 4/5/6/7,**排除年度結轉傳票本身**——結轉會把這些科目沖平,不排除的話
    已經結轉過的年度會整批顯示 0。"""
    lo, hi_full = f"{year}-01-01", f"{year}-12-31"
    today = dt.date.today()
    hi = min(hi_full, today.strftime("%Y-%m-%d")) if year == today.year else hi_full
    rows = q("""SELECT account_code, account_name, SUM(debit) db, SUM(credit) cr
                FROM ledger_entry
                WHERE entry_date BETWEEN ? AND ?
                  AND (account_code LIKE '4%' OR account_code LIKE '5%'
                       OR account_code LIKE '6%' OR account_code LIKE '7%')
                  AND source_type != 'year_closing'
                GROUP BY account_code, account_name
                HAVING SUM(debit)<>0 OR SUM(credit)<>0
                ORDER BY account_code""", (lo, hi))
    for r in rows:
        r["balance"] = (r["db"] or 0) - (r["cr"] or 0)
        r["disp"] = _display_balance(r["account_code"], r["balance"])
    net_income = -sum(r["balance"] for r in rows)
    closed = bool(q("""SELECT 1 FROM ledger_entry WHERE source_type='year_closing'
                        AND source_id=? LIMIT 1""", (year,)))
    return dict(year=year, window=f"{lo} ~ {hi_full}", rows=rows, net_income=net_income, closed=closed)


def _unclosed_pnl(as_of):
    """資產負債表用的「本期損益」顯示行:把**所有還沒結轉過的年度**(不是只算 as_of
    當年——如果前面有年度漏結轉,原始的收入/費用分錄還在總帳裡,不能只看 as_of 那一年,
    不然資產負債表會對不起來)的收入/費用類餘額全部加總。已經結轉過的年度不會重複算,
    因為那年的原始分錄雖然還在,但淨額已經真的轉進累積盈虧,這裡用
    `年份 NOT IN (已結轉年度)` 排除掉。"""
    r = q1("""SELECT COALESCE(SUM(debit),0) db, COALESCE(SUM(credit),0) cr
              FROM ledger_entry
              WHERE entry_date<=? AND source_type != 'year_closing'
                AND (account_code LIKE '4%' OR account_code LIKE '5%'
                     OR account_code LIKE '6%' OR account_code LIKE '7%')
                AND CAST(substr(entry_date,1,4) AS INTEGER) NOT IN (
                    SELECT source_id FROM ledger_entry WHERE source_type='year_closing')
           """, (as_of,))
    return -(r["db"] - r["cr"])


def balance_sheet(as_of):
    """資產負債表:試算表篩代碼開頭 1/2/3 的列;另外附上「本期損益」(見 `_unclosed_pnl`,
    顯示用,不是總帳裡真的科目)——年度結轉之前靠這個讓資產負債表配平,結轉之後這筆會
    自然趨近 0(因為淨額已經真的轉進累積盈虧了)。"""
    tb = trial_balance(as_of)
    year = int(as_of[:4])
    assets      = [r for r in tb["rows"] if r["account_code"][0] == "1"]
    liabilities = [r for r in tb["rows"] if r["account_code"][0] == "2"]
    equity      = [r for r in tb["rows"] if r["account_code"][0] == "3"]
    total_assets = sum(r["balance"] for r in assets)
    total_liab   = -sum(r["balance"] for r in liabilities)
    total_equity_posted = -sum(r["balance"] for r in equity)
    current_pnl = _unclosed_pnl(as_of)
    total_equity = total_equity_posted + current_pnl
    return dict(as_of=as_of, year=year, assets=assets, liabilities=liabilities, equity=equity,
                total_assets=total_assets, total_liab=total_liab,
                total_equity_posted=total_equity_posted, current_pnl=current_pnl,
                total_equity=total_equity,
                check=round(total_assets - (total_liab + total_equity), 2))
