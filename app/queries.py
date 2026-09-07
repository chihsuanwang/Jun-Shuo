"""儀表板 / 報表查詢。金額均為 NT$。
season=None 代表全部產季;asof 為『結算日』('latest'=最新訂單日 / 'today' / 'YYYY-MM-DD')。
毛利 = 銷售明細營收 - 明細成本(數量×批次單位成本);運費損益另計。"""
import datetime as dt
from db import q, q1


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


def season_of(d):
    """由日期(YYYY-MM-DD 或 date)推產季:4 月初~隔年 3 月底,以起始年命名。
    例:2026-04-01 ~ 2027-03-31 皆屬 2026 產季。"""
    s = d.isoformat() if hasattr(d, "isoformat") else str(d)
    y, m = int(s[:4]), int(s[5:7])
    return y if m >= 4 else y - 1


def as_of(asof=None):
    if asof and asof not in ("latest", ""):
        return dt.date.today().isoformat() if asof == "today" else asof
    r = q1('SELECT MAX(order_date) d FROM "order"')
    return r["d"] or dt.date.today().isoformat()


def _S(season, alias="o"):
    """回傳 (clause, params) —— 供 WHERE ... AND {clause} 使用。"""
    if season in (None, "all", "", 0):
        return "1=1", []
    return f"{alias}.season=?", [int(season)]


# ---------- KPI --------------------------------------------------------
def kpi(season=None, asof=None):
    a = as_of(asof)
    sc, sp = _S(season)
    rev = q1(f"SELECT COALESCE(SUM(order_total),0) v FROM \"order\" o WHERE {sc} AND order_kind='銷售'", sp)["v"]
    m = q1(f"""SELECT COALESCE(SUM(ol.line_subtotal),0) rev,
                      COALESCE(SUM(ol.qty*COALESCE(b.unit_cost,0)),0) cogs
               FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
               LEFT JOIN batch b ON b.batch_id=ol.batch_id
               WHERE {sc} AND o.order_kind='銷售'""", sp)
    gp = m["rev"] - m["cogs"]
    oc = q1(f"SELECT COUNT(*) n FROM \"order\" o WHERE {sc} AND order_kind='銷售'", sp)["n"]
    ship = q1(f"""SELECT COALESCE(SUM(shipping_fee_charged),0) charged,
                         COALESCE(SUM(shipping_cost_actual),0) cost
                  FROM "order" o WHERE {sc}""", sp)
    ar = q1(f"""SELECT COALESCE(SUM(order_total),0) v, COUNT(*) n FROM "order" o
                WHERE {sc} AND payment_status IN ('待收款','部分收款')""", sp)
    pr = q1(f"""SELECT COALESCE(SUM(ol.qty*COALESCE(b.unit_cost,0)),0) v
                FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                LEFT JOIN batch b ON b.batch_id=ol.batch_id
                WHERE {sc} AND o.order_kind<>'銷售'""", sp)["v"]
    return dict(
        revenue=rev, gross_profit=gp, margin=(gp / m["rev"] if m["rev"] else 0),
        orders=oc, aov=(rev / oc if oc else 0),
        ship_pnl=ship["charged"] - ship["cost"],
        ship_subsidy=((ship["cost"] - ship["charged"]) / ship["cost"] if ship["cost"] else 0),
        ar_amount=ar["v"], ar_count=ar["n"],
        pr_cost=pr, pr_ratio=(pr / rev if rev else 0),
        as_of=a,
    )


# ---------- 警示 -----------------------------------------------------
def alerts(season=None, asof=None):
    a = as_of(asof)
    sc, sp = _S(season)
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
    near = q1("""SELECT b.batch_code,
                        julianday(MIN(date(b.mfg_date,'+'||p.shelf_life_days||' days'))) - julianday(?) days
                 FROM batch b JOIN order_line ol ON ol.batch_id=b.batch_id
                 JOIN product p ON p.product_id=ol.product_id
                 GROUP BY b.batch_code HAVING days BETWEEN 0 AND 60
                 ORDER BY days LIMIT 1""", (a,))
    if near:
        out.append({"text": f"批 {near['batch_code']} 約 {near['days']:.0f} 天到期", "href": "/reports?tab=batch"})
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
def monthly(season=None):
    sc, sp = _S(season)
    return q(f"""SELECT strftime('%Y-%m', o.order_date) ym,
                        SUM(ol.line_subtotal) rev,
                        SUM(ol.line_subtotal - ol.qty*COALESCE(b.unit_cost,0)) gp
                 FROM "order" o JOIN order_line ol ON ol.order_id=o.order_id
                 LEFT JOIN batch b ON b.batch_id=ol.batch_id
                 WHERE {sc} AND o.order_kind='銷售'
                 GROUP BY ym ORDER BY ym""", sp)


def by_channel(season=None):
    sc, sp = _S(season)
    rows = q(f"""SELECT c.name,
        (SELECT COUNT(*) FROM "order" o WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') orders,
        (SELECT COALESCE(SUM(ol.line_subtotal),0) FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
           WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') rev,
        (SELECT COALESCE(SUM(ol.qty*COALESCE(b.unit_cost,0)),0) FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
           LEFT JOIN batch b ON b.batch_id=ol.batch_id
           WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') cogs,
        (SELECT COALESCE(SUM(o.platform_fee),0) FROM "order" o WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') pfee,
        (SELECT COALESCE(SUM(o.shipping_fee_charged - o.shipping_cost_actual),0) FROM "order" o
           WHERE o.channel_id=c.channel_id AND {sc} AND o.order_kind='銷售') ship_pnl
        FROM channel c""", sp * 5)
    for r in rows:
        r["gp"] = r["rev"] - r["cogs"] - r["pfee"]
    rows = [r for r in rows if r["orders"]]
    rows.sort(key=lambda r: r["rev"], reverse=True)
    return rows


def ar_aging(season=None, asof=None):
    a = as_of(asof)
    sc, sp = _S(season)
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


def batch_progress(season=None):
    sc, sp = _S(season, alias="b")
    return q(f"""SELECT b.batch_code, b.output_qty, b.mfg_date,
                        COALESCE((SELECT SUM(ol.qty) FROM order_line ol WHERE ol.batch_id=b.batch_id),0) sold
                 FROM batch b WHERE {sc} ORDER BY b.batch_code""", sp)


def issues_by_carrier(season=None):
    sc, sp = _S(season)
    return q(f"""SELECT o.carrier,
                        COUNT(DISTINCT o.order_id) shipped,
                        (SELECT COUNT(*) FROM shipment_issue si JOIN "order" o2 ON o2.order_id=si.order_id
                         WHERE o2.carrier=o.carrier) issues,
                        (SELECT COALESCE(SUM(cost_impact),0) FROM shipment_issue si JOIN "order" o2 ON o2.order_id=si.order_id
                         WHERE o2.carrier=o.carrier) loss
                 FROM "order" o WHERE {sc} AND o.carrier IS NOT NULL
                 GROUP BY o.carrier ORDER BY issues DESC""", sp)


def product_mix(season=None):
    sc, sp = _S(season)
    return q(f"""SELECT p.name, SUM(ol.line_subtotal) rev, SUM(ol.qty) qty
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 WHERE {sc} AND o.order_kind='銷售' AND ol.is_gift=0
                 GROUP BY p.name ORDER BY rev DESC""", sp)


def new_vs_repeat(season=None):
    sc, sp = _S(season)
    rows = q(f"""SELECT customer_id, COUNT(*) n, SUM(order_total) v
                 FROM "order" o WHERE {sc} AND order_kind='銷售' GROUP BY customer_id""", sp)
    return dict(
        repeat_rev=sum(r["v"] for r in rows if r["n"] > 1),
        new_rev=sum(r["v"] for r in rows if r["n"] == 1),
        repeat_customers=sum(1 for r in rows if r["n"] > 1),
        total_customers=len(rows),
    )


def top_customers(season=None, n=5):
    sc, sp = _S(season)
    return q(f"""SELECT cu.display_name, cu.segment, SUM(o.order_total) v
                 FROM "order" o JOIN customer cu ON cu.customer_id=o.customer_id
                 WHERE {sc} AND o.order_kind='銷售'
                 GROUP BY cu.customer_id ORDER BY v DESC LIMIT ?""", sp + [n])


# ---------- 訂單管理 --------------------------------------------
def orders_list(flt="all", kw=""):
    a = as_of()
    where, args = [], {"a": a}
    if flt == "overdue":
        where.append("o.payment_status='待收款' AND julianday(:a)-julianday(o.order_date) > 30")
    elif flt == "unpaid":
        where.append("o.payment_status IN ('待收款','部分收款')")
    elif flt == "unshipped":
        where.append("o.ship_status='待出貨' AND julianday(:a)-julianday(o.order_date) > 3")
    if kw:
        where.append("(o.order_no LIKE :k OR cu.display_name LIKE :k)")
        args["k"] = f"%{kw}%"
    sql = """SELECT o.order_id, o.order_no, o.order_date, o.order_kind, o.order_total,
                    o.payment_status, o.ship_status, cu.display_name cust,
                    (SELECT c.name FROM channel c WHERE c.channel_id=o.channel_id) chan,
                    CAST(julianday(:a)-julianday(o.order_date) AS INT) age
             FROM "order" o LEFT JOIN customer cu ON cu.customer_id=o.customer_id"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY o.order_date DESC, o.order_id DESC LIMIT 400"
    return q(sql, args)


def order_get(oid):
    o = q1("""SELECT o.*, cu.display_name cust,
                     (SELECT name FROM channel WHERE channel_id=o.channel_id) chan
              FROM "order" o LEFT JOIN customer cu ON cu.customer_id=o.customer_id
              WHERE o.order_id=?""", (oid,))
    lines = q("""SELECT ol.*, p.name pname, p.uom, b.batch_code
                 FROM order_line ol JOIN product p ON p.product_id=ol.product_id
                 LEFT JOIN batch b ON b.batch_id=ol.batch_id
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
def profit_by_product(season=None):
    sc, sp = _S(season)
    return q(f"""SELECT p.name, SUM(ol.qty) qty, p.uom,
                        SUM(ol.line_subtotal) rev,
                        SUM(CASE WHEN ol.list_price>ol.unit_price
                                 THEN (ol.list_price-ol.unit_price)*ol.qty ELSE 0 END) disc,
                        AVG(NULLIF(b.unit_cost,0)) unit_cost,
                        SUM(ol.line_subtotal - ol.qty*COALESCE(b.unit_cost,0)) gp,
                        AVG(NULLIF(ol.unit_price,0)) avg_price,
                        AVG(NULLIF(ol.list_price,0)) list_price
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 LEFT JOIN batch b ON b.batch_id=ol.batch_id
                 WHERE {sc} AND o.order_kind='銷售' AND ol.is_gift=0
                 GROUP BY p.name ORDER BY rev DESC""", sp)


# ---------- 報表:批次與效期 -----------------------------------
def batch_report(season=None, asof=None):
    a = as_of(asof)
    sc, sp = _S(season, alias="b")
    rows = q(f"""SELECT b.batch_code, b.season, b.output_qty, b.output_uom, b.mfg_date,
                        b.unit_cost,
                        (SELECT p.name FROM product p WHERE p.product_id=b.product_id) pname,
                        COALESCE(
                          (SELECT p.shelf_life_days FROM product p WHERE p.product_id=b.product_id),
                          (SELECT MIN(p.shelf_life_days) FROM order_line ol JOIN product p
                             ON p.product_id=ol.product_id WHERE ol.batch_id=b.batch_id)
                        ) shelf,
                        COALESCE((SELECT SUM(ol.qty) FROM order_line ol WHERE ol.batch_id=b.batch_id),0) sold
                 FROM batch b WHERE {sc} ORDER BY b.batch_code""", sp)
    for r in rows:
        r["remain"] = (r["output_qty"] or 0) - r["sold"]
        r["rate"] = (r["sold"] / r["output_qty"]) if r["output_qty"] else 0
        r["remain_value"] = r["remain"] * (r["unit_cost"] or 0)
        if r["mfg_date"] and r["shelf"]:
            exp = (dt.date.fromisoformat(r["mfg_date"]) + dt.timedelta(days=r["shelf"]))
            r["expiry"] = exp.isoformat()
            r["days_left"] = (exp - dt.date.fromisoformat(a)).days
        else:
            r["expiry"], r["days_left"] = None, None
    return rows


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
OPEX_CATS = ["人事", "場地・倉儲", "行銷", "金流手續費", "設備維護", "培訓", "其他"]

def _month_span(a, b):
    y, m = map(int, a.split("-")); y2, m2 = map(int, b.split("-"))
    out = []
    while (y, m) <= (y2, m2):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m = 1; y += 1
    return out

def months_with_data():
    """有訂單 或 有登錄營運費用 的月份;並把中間的空月補上(淡月照樣有支出)。"""
    oms = [r["ym"] for r in q("SELECT DISTINCT strftime('%Y-%m', order_date) ym FROM \"order\"")]
    ems = [r["ym"] for r in q("SELECT DISTINCT ym FROM op_expense")]
    allm = sorted(set(oms) | set(ems))
    if not allm:
        t = dt.date.today()
        return [f"{t.year}-{t.month:02d}"]
    return _month_span(allm[0], allm[-1])

def finance_month(ym):
    rev = q1("""SELECT COALESCE(SUM(order_total),0) v FROM "order"
                WHERE order_kind='銷售' AND strftime('%Y-%m',order_date)=?""", (ym,))["v"]
    cogs = q1("""SELECT COALESCE(SUM(ol.qty*COALESCE(b.unit_cost,0)),0) v
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 LEFT JOIN batch b ON b.batch_id=ol.batch_id
                 WHERE o.order_kind='銷售' AND strftime('%Y-%m',o.order_date)=?""", (ym,))["v"]
    plat = q1("""SELECT COALESCE(SUM(platform_fee),0) v FROM "order"
                 WHERE order_kind='銷售' AND strftime('%Y-%m',order_date)=?""", (ym,))["v"]
    ship = q1("""SELECT COALESCE(SUM(shipping_fee_charged - shipping_cost_actual),0) v
                 FROM "order" WHERE strftime('%Y-%m',order_date)=?""", (ym,))["v"]
    opex = q("""SELECT category, SUM(amount) amt FROM op_expense
                WHERE ym=? GROUP BY category ORDER BY amt DESC""", (ym,))
    opex_total = sum(r["amt"] for r in opex)
    gp = rev - cogs
    margin = gp / rev if rev else 0
    pretax = gp - plat + ship - opex_total
    breakeven = (opex_total / margin) if margin else None
    return dict(ym=ym, revenue=rev, cogs=cogs, gross_profit=gp, margin=margin,
                platform_fee=plat, ship_pnl=ship, opex=opex, opex_total=opex_total,
                pretax=pretax, breakeven=breakeven)

def finance_trend():
    out = []
    for ym in months_with_data():
        d = finance_month(ym)
        out.append(dict(ym=ym, pretax=d["pretax"], revenue=d["revenue"],
                        opex_total=d["opex_total"]))
    return out

def season_finance(season):
    """整個產季合計:銷售月的營收 − 產季 12 個月(4 月初~隔年 3 月底)的營運費用。"""
    rev = q1("""SELECT COALESCE(SUM(order_total),0) v FROM "order"
                WHERE season=? AND order_kind='銷售'""", (season,))["v"]
    cogs = q1("""SELECT COALESCE(SUM(ol.qty*COALESCE(b.unit_cost,0)),0) v
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 LEFT JOIN batch b ON b.batch_id=ol.batch_id
                 WHERE o.season=? AND o.order_kind='銷售'""", (season,))["v"]
    plat = q1("""SELECT COALESCE(SUM(platform_fee),0) v FROM "order"
                 WHERE season=? AND order_kind='銷售'""", (season,))["v"]
    ship = q1("""SELECT COALESCE(SUM(shipping_fee_charged - shipping_cost_actual),0) v
                 FROM "order" WHERE season=?""", (season,))["v"]
    lo, hi = f"{season}-04", f"{season + 1}-03"
    ox = q1("""SELECT COALESCE(SUM(amount),0) v, COUNT(DISTINCT ym) n
               FROM op_expense WHERE ym >= ? AND ym <= ?""", (lo, hi))
    gp = rev - cogs
    return dict(season=season, window=f"{lo} ~ {hi}",
                revenue=rev, cogs=cogs, gross_profit=gp, platform_fee=plat, ship_pnl=ship,
                opex=ox["v"], opex_months=ox["n"],
                pretax=gp - plat + ship - ox["v"],
                margin=(gp / rev if rev else 0))


def finance_summary(season=None):
    """儀表板用:單一產季 → season_finance;全部 → 各產季相加。"""
    if season not in (None, "all", "", 0):
        return season_finance(int(season))
    ss = seasons()
    if not ss:
        return dict(season=None, window="全部產季", revenue=0, cogs=0, gross_profit=0,
                    platform_fee=0, ship_pnl=0, opex=0, opex_months=0, pretax=0, margin=0)
    parts = [season_finance(s) for s in ss]
    agg = dict(season=None, window="全部產季")
    for f in ("revenue", "cogs", "gross_profit", "platform_fee", "ship_pnl", "opex", "opex_months", "pretax"):
        agg[f] = sum(p[f] for p in parts)
    agg["margin"] = agg["gross_profit"] / agg["revenue"] if agg["revenue"] else 0
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
    """產季目標達成 / 依速度預估季末 / 損益兩平線。season=None → 取最新產季。"""
    if season in (None, "all", "", 0):
        # 取「最近一個有銷售的產季」(而非只有前期支出、還沒開賣的那個)
        sold = [r["season"] for r in
                q("SELECT DISTINCT season FROM \"order\" WHERE order_kind='銷售' ORDER BY season")]
        ss = seasons()
        season = sold[-1] if sold else (ss[-1] if ss else None)
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
        # 只列「還有正庫存」的批次,依製造日(舊到新 = 先出)排序
        r["by_batch"] = q("""SELECT COALESCE(b.batch_code,'(未指定)') batch_code,
                                    SUM(sm.qty) qty,
                                    MAX(b.mfg_date) mfg_date
                             FROM stock_move sm LEFT JOIN batch b ON b.batch_id=sm.batch_id
                             WHERE sm.product_id=? GROUP BY sm.batch_id
                             HAVING SUM(sm.qty) > 0.0001
                             ORDER BY mfg_date""", (r["product_id"],))
        r["oldest"] = r["by_batch"][0] if r["by_batch"] else None
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
    return q(f"""SELECT sm.*, p.sku, p.name pname, p.uom,
                        b.batch_code, o.order_no
                 FROM stock_move sm
                 JOIN product p ON p.product_id=sm.product_id
                 LEFT JOIN batch b ON b.batch_id=sm.batch_id
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
        o["lines"] = q("""SELECT p.name pname, p.uom, p.type_code_raw spec, ol.qty, b.batch_code
                          FROM order_line ol JOIN product p ON p.product_id=ol.product_id
                          LEFT JOIN batch b ON b.batch_id=ol.batch_id
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
