"""家易照舊填熟悉的業務表單,這裡依對照表把送出的內容組成一組複式分錄,自動記進
app/schema.sql 的 ledger_entry(總帳)。

**2026-10-08 改成貿易公司模式(同仁試用回饋)**:郡碩沒有存貨,訂單成立當下才向小農
進貨、轉手賣給客人。訂單成立分錄 = 借應收帳款 / 貸銷貨收入 + 銷項稅額(家易手動填,
售價含稅),同時借銷貨成本 / 貸應付帳款(欠小農,小農只有一個不分人)。付小農貨款另外
記(`compose_farmer_payment_entries`)。進貨、庫存、生產入庫、銷貨退回/折讓整批拿掉;
退貨退款 = 直接刪訂單;只退款不退貨 = 其他損失;退貨不退款 = 貨退給小農、小農退成本
(沖應付帳款 / 銷貨成本)。下面幾段是改版前的沿革,提到進貨/存貨/生產入庫的部分已不適用。

- 進貨 / 營運費用(方案 B 試點):存檔前在確認畫面給他看、可微調
  (2026-10-17 起營運支出不經確認頁,同頁預覽分錄)。排除設備採購,那走固定資產,這批不記分錄。
- 訂單 / 銷售(回合二):不經過確認畫面,存檔時背景自動記——訂單是最高頻操作,
  不想多插一個步驟。一張訂單對到兩張獨立傳票:成立時記的 order_sale(應收帳款/
  銷貨收入/銷貨成本/存貨,不管收沒收到錢)、收款時記的 order_payment(現金/應收帳款,
  只在有實收金額時才有)。
- 9 宮格 B 類(回合三):生產入庫 / 其他收益 / 資本異動 / 帳務調整,一樣不經過確認畫面
  ——這批是從零蓋的新畫面,欄位直接放在同一張表單裡,存檔即過帳。「其他費用」沒有另開
  畫面,併進「營運費用」既有畫面的類別下拉(EXPENSE_ACCOUNTS 補了對應子科目)。
- 固定資產(技術債 #1,2026-09-21):買入比照其他類型,存檔整張重開(`save_voucher`)。
  折舊沒有「存檔」這種天然觸發點(是時間流逝就該發生的事),改用 `save_voucher_if_new`
  ——開 `/assets` 頁時系統順便把還沒記過的月份補上,已經記過的月份不動,不是整張重開。
- 銷貨退回 / 折讓(技術債 #2,2026-09-21):比照訂單,不經過確認畫面,存檔背景自動記。
  沖銷方向依原訂單付款狀態:已收款貸現金/銀行存款(真退錢),沒收款貸應收帳款(折抵)。
- 正式三表(試算表/資產負債表/綜合損益表,2026-09-23):給同事做帳/報稅用,直接從
  ledger_entry 彙總,不重用業務表算法(queries.season_finance 那套是給家易看的經營報表,
  口徑不用跟總帳一致)。年度用西曆年(1~12月)切,跟系統其他地方用的「產季」是兩條不同
  時間軸。年度結轉是真的貼一筆傳票(`compose_year_closing_entries`),把 4~7 開頭科目的
  當年餘額歸零、淨額轉入累積盈虧,不是報表端純公式計算——所以要防重複結轉(main.py 存檔前
  查 source_id=year 是否已存在)。銀行帳戶改成查 `bank_account` 主檔,每家銀行有自己的
  固定子代碼(1103-01/02/03…),第一次用到某個名字時 `_cash_account()` 自動建號,不用
  家易先手動開戶。

對照表(PURCHASE_ACCOUNTS / EXPENSE_ACCOUNTS / REVENUE_ACCOUNTS / COGS_INVENTORY_ACCOUNTS /
OTHER_INCOME_ACCOUNTS / EQUITY_* / FIXED_ASSET_ACCOUNTS / RETURN_ACCOUNTS 等)2026-09-21
已對照同事「會計科目表」正式版(完整科目清單)逐一核對過一次,原本猜的幾個科目代碼
(1150/1303/5901/5909、6111~6190 那一批)正式表裡根本沒有,已經改成正式表裡真的存在的
代碼。但「這個類別該算哪個科目」還是我依常理判斷,不是跟會計師/同事對過的——尤其是標了
「待確認」註解的那幾個(田間管理、法定盈餘公積),正式表完全沒有對應科目,先接在最近的
科目下面,要請同事確認。「研發」原本也在這份待確認名單,2026-09-21 已確認申報表那筆
78 萬其實是設備零件費用,不是本科目該記的東西(見 EXPENSE_ACCOUNTS 上面的註解)。之後
要改直接改這個檔案的常數就好,比照 queries.OPEX_CATS 也是這樣讓人直接改的做法。
2026-09-23 同事又補了一份會計科目表更新檔,原本 3 個「待確認」科目(田間管理/法定盈餘
公積/運輸設備)這份更新檔還是沒有列出對應代碼,仍要問同事;但確認了現有 6188-01~10
那組子科目猜對了,另外多列出 8 個原本系統沒用到的獨立費用科目(文具用品/交際費/捐贈/
伙食費/職工福利/佣金支出/進出口費用/產品保固費用),已補進 EXPENSE_ACCOUNTS 跟
queries.OPEX_CATS。同一天同事又給了正式三表的範例,連帶又給了一批新代碼(7101 利息收入/
7190 其他收入/7250 生物資產淨FV利益/7510 利息費用),取代原本代打用的 4881-01/02/09、
6498——PRODUCTION_GAIN_ACCOUNT、OTHER_INCOME_ACCOUNTS 都已經改成新代碼。

2026-10-01:「田間管理」從「待確認科目」名單**拿掉**(不是找到代碼了,是確認這個類別
在公司帳上根本不該存在)——使用者說明商業實質是公司跟小農(合作社)採購,小農自己種、
自己承擔田間管理成本,公司帳上只會有一筆「採購」。EXPENSE_ACCOUNTS 移除這個類別,
queries.OPEX_CATS 的下拉也拿掉,seed.py 對應的示範資料改用既有的「中寮果農合作社」
採購(原料 / 農民收據)取代。剩 3 個待確認:運輸設備、法定盈餘公積、3351-03。
"""
from db import q, q1, execute
import chart

# 2026-10-16 起:科目代碼照同事「記帳系統_1015.xlsx」帳務設定表,科目表本身存在資料庫
# (chart.py / account 表,「會計科目設定」頁可以改)。下面這些常數只決定「哪個角色用哪個
# 代碼」,實際寫進總帳的科目名稱一律在 save_voucher() 依科目表重新帶一次(named_legs),
# 所以在設定頁改名,新傳票就會用新名字。

CASH_ACCOUNT = ("1102", "現金")
TAX_INPUT_ACCOUNT = ("1423", "進項稅額")   # 原本誤用 1150(正式表沒有這個碼);1423 才是進項稅額

# 營運費用類別(對應 queries.OPEX_CATS)-> 借方費用科目。直接人工/人事共用「薪資支出」
# (6110),用子科目分開,比照正式表 6188 那組子科目的做法。「研發」類別 2026-09-21 已確認:
# 申報表那筆 78 萬研發費其實是烘焙機器的零件/組裝費用,該走固定資產(逐年提折舊),不該算
# 營運費用——這個類別留著給真正沒有形成實體設備的費用用(配方試驗、委外檢驗等),
# finance_expense_form.html 選到「研發」會提醒使用者評估是否該改登固定資產。
#
# 「田間管理」2026-10-01 已經**拿掉**,不是改掛別的科目:郡碩的商業實質是公司跟小農
# (中寮果農合作社)採購農產品/加工品,小農自己生產、自己承擔田間管理成本;公司只負責
# 檢驗/包裝/行銷/銷售,不會有一筆「公司自己做田間管理」的費用。這筆成本已經包含在付給
# 小農的採購價裡——2026-10-08 起是訂單成立時自動記的「銷貨成本 / 應付帳款」,
# 不需要另一個營運費用科目。
EXPENSE_ACCOUNTS = {
    "直接人工":   ("6110-01", "薪資支出-直接人工"),
    "人事":       ("6110-02", "薪資支出-人事"),
    "運費":       ("6188-11", "其他費用-運費(待確認)"),
    "驗證費":     ("6188-07", "其他費用-檢驗費"),
    "研發":       ("6188-12", "其他費用-研發"),
    "場地・倉儲": ("6111", "租金支出"),
    "行銷":       ("6117", "廣告費"),
    "金流手續費": ("6188-04", "其他費用-手續費"),
    "設備維護":   ("6116", "修繕費"),
    "培訓":       ("6131", "訓練費"),
    "其他":       ("6188", "其他費用"),
    # 9 宮格「其他費用」併進來的子科目(同事會計科目表 6188-01~10)
    "其他費用-什項購置": ("6188-01", "其他費用-什項購置"),
    "其他費用-勞務費":   ("6188-02", "其他費用-勞務費"),
    "其他費用-設計費":   ("6188-03", "其他費用-設計費"),
    "其他費用-手續費":   ("6188-04", "其他費用-手續費"),
    "其他費用-交通費":   ("6188-05", "其他費用-交通費"),
    "其他費用-包裝費":   ("6188-06", "其他費用-包裝費"),
    "其他費用-檢驗費":   ("6188-07", "其他費用-檢驗費"),
    "其他費用-印刷費":   ("6188-08", "其他費用-印刷費"),
    "其他費用-規費":     ("6188-09", "其他費用-規費"),
    "其他費用-燃料費":   ("6188-10", "其他費用-燃料費"),
    # 同事會計科目表 2026-09-23 更新的獨立費用科目(不是 6188 底下的子科目,自成一號)
    "文具用品":   ("6112", "文具用品"),
    "交際費":     ("6120", "交際費"),
    "捐贈":       ("6121", "捐贈"),
    "伙食費":     ("6127", "伙食費"),
    "職工福利":   ("6128", "職工福利"),
    "佣金支出":   ("6130", "佣金支出"),
    "進出口費用": ("6132", "進出口費用"),
    "產品保固費用": ("613501", "產品保固費用"),
    # 同事會計科目表 2026-09-23 更新新增,目前系統沒有專門的「借款」畫面,先併進營運費用選
    "利息費用": ("7510", "利息費用"),
}

# 應收帳款(訂單成立時的借方、收款時的貸方)
AR_ACCOUNT = ("1172", "應收帳款")

# 銷貨成本(COGS 借方)——不依產品線拆,同事「會計科目表」正式版裡只有一條
COGS_EXPENSE_ACCOUNT = ("5111", "銷貨成本")

# 應付帳款(欠小農):開訂單時記成本「借 採購成本 / 貸 應付帳款」,同一張傳票馬上
# 「借 應付帳款 / 貸 現金」付掉(接單當天就付小農)。小農只有一個,不分人。
# (同事會計場景對應分錄表_1015 場景 1、2)
AP_ACCOUNT = ("2171", "應付帳款")

# 產品線 -> 採購成本科目(VA 組,開單時的成本借方)
COST_ACCOUNTS = {
    "龍眼鮮果": ("5111-01", "採購成本-龍眼鮮果"),
    "龍眼乾":   ("5111-02", "採購成本-龍眼乾"),
    "龍眼肉":   ("5111-03", "採購成本-龍眼肉"),
    "龍眼花":   ("5111-04", "採購成本-龍眼花"),
    "蜂蜜":     ("5111-05", "採購成本-龍眼蜂蜜"),
    "龍眼蜂蜜": ("5111-05", "採購成本-龍眼蜂蜜"),
}
COST_DEFAULT = ("5111-02", "採購成本-龍眼乾")

# 只退款不退貨(2026-10-08):不管退多少一律算其他支出。同事會計科目表沒有「其他支出」,
# 先用最接近的 6499 其他損失;之後拿到完整科目表再改這裡就好。
REFUND_LOSS_ACCOUNT = ("6188-99", "其他費用-其他")   # 2026-10-16:暫定,待同仁確認

# 產品線 -> 銷貨收入科目(貸方)。沒歸屬產品線的商品用 REVENUE_DEFAULT。
REVENUE_ACCOUNTS = {
    "龍眼鮮果": ("4111-01", "銷貨收入-龍眼鮮果"),
    "龍眼乾":   ("4111-02", "銷貨收入-龍眼乾"),
    "龍眼肉":   ("4111-03", "銷貨收入-龍眼肉"),
    "龍眼花":   ("4111-04", "銷貨收入-龍眼花"),     # 2026-10-16 新增產品線
    "蜂蜜":     ("4111-05", "銷貨收入-龍眼蜂蜜"),   # 1015 版起蜂蜜改成 -05
    "龍眼蜂蜜": ("4111-05", "銷貨收入-龍眼蜂蜜"),
}
REVENUE_DEFAULT = ("4110", "銷貨收入")

def _cash_account(payment_account):
    """現金或某家銀行的科目代碼。2026-10-16 起銀行帳戶是會計科目表 C 組裡的科目,
    找不到時自動接著編號新增,見 chart.cash_account()。"""
    return chart.cash_account(payment_account)


def compose_expense_entries(account, amount, tax_amount=0, payment_account=None):
    """營運支出(VD)/ 其他費用(VE):借 費用科目 + 進項稅額 / 貸 現金或銀行。
    account 是科目代碼(2026-10-16 起表單直接選科目);舊資料傳的是類別名稱時,退回舊對照表。"""
    amount = amount or 0
    tax_amount = tax_amount or 0
    if q1("SELECT 1 FROM account WHERE code=?", (account,)):
        code, name = account, chart.name_of(account)
    else:
        code, name = EXPENSE_ACCOUNTS.get(account, ("6188-99", "其他費用-其他"))
    cash_code, cash_name = _cash_account(payment_account)
    legs = [dict(account_code=code, account_name=name, debit=amount, credit=0)]
    if tax_amount:
        legs.append(dict(account_code=TAX_INPUT_ACCOUNT[0], account_name=TAX_INPUT_ACCOUNT[1],
                          debit=tax_amount, credit=0))
    legs.append(dict(account_code=cash_code, account_name=cash_name, debit=0, credit=amount + tax_amount))
    return legs


def compose_order_sale_entries(lines, order_kind, shipping_fee=0, sales_tax=0, cost_payment_account=None):
    """訂單成立分錄(同事「會計場景對應分錄表_1015」場景 1「成功賣出貨物時,但還未收款」):
    借 1172 應收帳款(含稅、含跟客人收的運費)/ 貸 2214 銷項稅額 + 貸 4111-xx 銷貨收入(依產品線);
    借 5111-xx 採購成本(依產品線)/ 貸 2171 應付帳款(欠小農);
    接著馬上「進口商品:實際付款」借 2171 應付帳款 / 貸 現金或銀行(cost_payment_account)。
    2026-10-16 使用者決定「接單當天就付錢給小農」(無存貨、接單才買),所以場景 1、2 在同一張傳票
    一起記,應付帳款一進一出永遠是 0,不需要另外的付款頁。
    - lines = [(產品線名稱或 None, 含稅小計, 成本), …]。成本是家易在每個品項自己填的金額
      (2026-10-16:系統不算,商品目錄也不再有單位成本)。
    - 跟客人收的運費依各線含稅金額佔比分進收入,家易填的銷項稅額也依同樣佔比扣掉,
      尾差併進金額最大的那條線。運費支出不在這裡記,家易從營運支出記 6114 運費。
    order_kind 不是「銷售」(贈送/樣品/內部領用等)回傳空 list,不記這筆。"""
    if order_kind != "銷售":
        return []
    gross, cost = {}, {}
    for pg_name, amt, c in lines:
        gross[pg_name] = gross.get(pg_name, 0) + (amt or 0)
        cost[pg_name] = cost.get(pg_name, 0) + (c or 0)
    goods = sum(gross.values())
    shipping_fee = shipping_fee or 0
    sales_tax = sales_tax or 0
    total = goods + shipping_fee
    total_cost = sum(cost.values())
    if total <= 0 and total_cost <= 0:
        return []
    legs = []
    if total > 0:
        g = gross if goods else {(next(iter(gross)) if gross else None): 0}
        net = {}
        for pg, amt in g.items():
            share = (amt / goods) if goods else 1 / len(g)
            net[pg] = round(amt + shipping_fee * share - sales_tax * share, 2)
        diff = round(total - sales_tax - sum(net.values()), 2)
        if diff:
            biggest = max(net, key=lambda k: g[k])
            net[biggest] = round(net[biggest] + diff, 2)
        legs.append(dict(account_code=AR_ACCOUNT[0], account_name=AR_ACCOUNT[1], debit=total, credit=0))
        if sales_tax:
            legs.append(dict(account_code=SALES_TAX_OUTPUT_ACCOUNT[0], account_name=SALES_TAX_OUTPUT_ACCOUNT[1],
                              debit=0, credit=sales_tax))
        for pg, amt in net.items():
            if amt:
                code, name = REVENUE_ACCOUNTS.get(pg, REVENUE_DEFAULT)
                legs.append(dict(account_code=code, account_name=name, debit=0, credit=amt))
    if total_cost > 0:
        for pg, c in cost.items():
            if c:
                code, name = COST_ACCOUNTS.get(pg, COST_DEFAULT)
                legs.append(dict(account_code=code, account_name=name, debit=c, credit=0))
        legs.append(dict(account_code=AP_ACCOUNT[0], account_name=AP_ACCOUNT[1], debit=0, credit=total_cost))
        cash_code, cash_name = _cash_account(cost_payment_account)
        legs.append(dict(account_code=AP_ACCOUNT[0], account_name=AP_ACCOUNT[1], debit=total_cost, credit=0))
        legs.append(dict(account_code=cash_code, account_name=cash_name, debit=0, credit=total_cost))
    return legs


def compose_order_payment_entries(paid_amount, payment_account=None):
    """收款分錄:現金(依訂單的付款帳戶)/ 應收帳款。沒收到錢(paid_amount<=0)回傳空 list。"""
    paid_amount = paid_amount or 0
    if paid_amount <= 0:
        return []
    cash_code, cash_name = _cash_account(payment_account)
    return [
        dict(account_code=cash_code, account_name=cash_name, debit=paid_amount, credit=0),
        dict(account_code=AR_ACCOUNT[0], account_name=AR_ACCOUNT[1], debit=0, credit=paid_amount),
    ]


def compose_goods_purchase_entries(amount, payment_account=None):
    """進口商品 = 付錢給小農(會計場景對應分錄表_1015 場景 2「實際付款」):
    借 2171 應付帳款 / 貸 現金或銀行。"""
    amount = amount or 0
    if amount <= 0:
        return []
    cash_code, cash_name = _cash_account(payment_account)
    return [
        dict(account_code=AP_ACCOUNT[0], account_name=AP_ACCOUNT[1], debit=amount, credit=0),
        dict(account_code=cash_code, account_name=cash_name, debit=0, credit=amount),
    ]


# ---------- 9 宮格 B 類(其他收益 / 資本異動 / 帳務調整)------

# 其他收益的銷項稅額(貸方)——跟進貨/費用的進項稅額(借方)相對
SALES_TAX_OUTPUT_ACCOUNT = ("2214", "銷項稅額")

# 其他收益類別 -> 貸方收入科目。2026-09-23 同事更新檔給了專門科目,不再掛在 4881 底下。
OTHER_INCOME_ACCOUNTS = {
    "利息收入":     ("7101", "利息收入"),
    "政府補助收入": ("7190-01", "其他收入-政府補助"),
    "其他":         ("7190-99", "其他收入-其他"),
}

# 資本異動科目
EQUITY_STOCK_ACCOUNT    = ("3110", "普通股股本")
EQUITY_RESERVE_ACCOUNT  = ("3310-03", "法定盈餘公積")
EQUITY_RETAINED_ACCOUNT = ("3351-03", "累積盈虧")

# 固定資產類別(對應 main.ASSET_CATS)-> 借方資產科目。正式表沒有「生財器具」「運輸設備」
# 專門科目,電腦設備/生財器具先併記辦公設備,運輸設備標「待確認」。
FIXED_ASSET_ACCOUNTS = {
    "機器設備":   ("1616", "機器設備"),
    "房屋建築":   ("1611", "房屋"),
    "電腦設備":   ("1691", "辦公設備"),
    "生財器具":   ("1691", "辦公設備"),
    "運輸設備":   ("1616-02", "機器設備-運輸設備(待確認)"),
    "其他":       ("1616", "機器設備"),
}
FIXED_ASSET_DEFAULT = ("1616", "機器設備")

# 折舊費用(借方)/累計折舊(貸方,共用一個科目,不分資產類別——正式表沒有專門對應,
# 比照正式表自己的 1405生產性生物資產/1406累計折舊-生產性生物資產那組命名模式類推)
DEPRECIATION_EXPENSE_ACCOUNT = ("6124", "折舊")
ACCUM_DEP_ACCOUNT = ("1618", "累計折舊-機器設備")


def compose_other_income_entries(category, amount, tax_amount=0, payment_account=None):
    """其他收益:借現金(含稅)/貸收入 + 貸銷項稅額(有稅才加)。"""
    amount = amount or 0
    tax_amount = tax_amount or 0
    if amount <= 0 and tax_amount <= 0:
        return []
    if q1("SELECT 1 FROM account WHERE code=?", (category,)):   # 2026-10-16 起傳的是 UB 科目代碼
        code, name = category, chart.name_of(category)
    else:
        code, name = OTHER_INCOME_ACCOUNTS.get(category, OTHER_INCOME_ACCOUNTS["其他"])
    cash_code, cash_name = _cash_account(payment_account)
    legs = [dict(account_code=cash_code, account_name=cash_name, debit=amount + tax_amount, credit=0),
            dict(account_code=code, account_name=name, debit=0, credit=amount)]
    if tax_amount:
        legs.append(dict(account_code=SALES_TAX_OUTPUT_ACCOUNT[0], account_name=SALES_TAX_OUTPUT_ACCOUNT[1],
                          debit=0, credit=tax_amount))
    return legs


def compose_equity_entries(txn_type, amount, payment_account=None):
    """資本異動:現金增資(借現金/貸股本)、盈餘轉列公積(借累積盈虧/貸法定盈餘公積,
    不涉及現金)。"""
    amount = amount or 0
    if amount <= 0:
        return []
    if txn_type == "現金增資":
        cash_code, cash_name = _cash_account(payment_account)
        return [
            dict(account_code=cash_code, account_name=cash_name, debit=amount, credit=0),
            dict(account_code=EQUITY_STOCK_ACCOUNT[0], account_name=EQUITY_STOCK_ACCOUNT[1],
                 debit=0, credit=amount),
        ]
    if txn_type == "盈餘轉列公積":
        return [
            dict(account_code=EQUITY_RETAINED_ACCOUNT[0], account_name=EQUITY_RETAINED_ACCOUNT[1],
                 debit=amount, credit=0),
            dict(account_code=EQUITY_RESERVE_ACCOUNT[0], account_name=EQUITY_RESERVE_ACCOUNT[1],
                 debit=0, credit=amount),
        ]
    return []


def compose_manual_entries(debit_code, debit_name, credit_code, credit_name, amount):
    """帳務調整:其他 8 類都涵蓋不到時,直接指定一組借/貸科目手動記一筆。"""
    amount = amount or 0
    if amount <= 0 or not (debit_code and credit_code):
        return []
    return [
        dict(account_code=debit_code, account_name=debit_name, debit=amount, credit=0),
        dict(account_code=credit_code, account_name=credit_name, debit=0, credit=amount),
    ]


def compose_asset_acquire_entries(category, cost, tax_amount=0, payment_account=None):
    """設備買入(1015 版帳務設定表「設備相關」):借 1616 機器設備 + 1423 進項稅額 / 貸 現金或銀行。
    2026-10-16 起沒有政府補助這欄;K 組只有機器設備一個科目,類別只是描述用。"""
    cost = cost or 0
    tax_amount = tax_amount or 0
    if cost <= 0:
        return []
    code, name = FIXED_ASSET_DEFAULT
    cash_code, cash_name = _cash_account(payment_account)
    legs = [dict(account_code=code, account_name=name, debit=cost, credit=0)]
    if tax_amount:
        legs.append(dict(account_code=TAX_INPUT_ACCOUNT[0], account_name=TAX_INPUT_ACCOUNT[1],
                          debit=tax_amount, credit=0))
    legs.append(dict(account_code=cash_code, account_name=cash_name, debit=0, credit=cost + tax_amount))
    return legs


def compose_depreciation_entries(monthly_amount):
    """折舊(每月一筆):借折舊費用/貸累計折舊。"""
    monthly_amount = monthly_amount or 0
    if monthly_amount <= 0:
        return []
    return [
        dict(account_code=DEPRECIATION_EXPENSE_ACCOUNT[0], account_name=DEPRECIATION_EXPENSE_ACCOUNT[1],
             debit=monthly_amount, credit=0),
        dict(account_code=ACCUM_DEP_ACCOUNT[0], account_name=ACCUM_DEP_ACCOUNT[1],
             debit=0, credit=monthly_amount),
    ]


RETURN_KINDS = ("退款", "退回小農")


def compose_sales_return_entries(kind, amount, already_paid, payment_account=None, cost_account=None):
    """訂單的退款 / 退貨(2026-10-16 起照 1015 版的成本做法):
    - 「退款」= 只退款不退貨,不管退多少一律算其他支出(暫記 6188-99,待同仁確認):
      借 其他支出 / 貸 現金或銀行(已收款,真的退錢)或應收帳款(還沒收款,少收這筆)。
    - 「退回小農」= 退貨但不退款,貨退給小農、小農把錢退回帳戶(2026-10-16 起接單當天就付小農,
      所以退回來的是現金):借 現金或銀行 / 貸 採購成本(cost_account,VA 組)。
    退貨又退款不走這裡,直接刪訂單(總帳一起刪)。"""
    amount = amount or 0
    if amount <= 0:
        return []
    cash_code, cash_name = _cash_account(payment_account)
    if kind == "退回小農":
        cost_account = cost_account or COST_DEFAULT[0]
        return [
            dict(account_code=cash_code, account_name=cash_name, debit=amount, credit=0),
            dict(account_code=cost_account, account_name=chart.name_of(cost_account), debit=0, credit=amount),
        ]
    legs = [dict(account_code=REFUND_LOSS_ACCOUNT[0], account_name=REFUND_LOSS_ACCOUNT[1],
                 debit=amount, credit=0)]
    if already_paid:
        legs.append(dict(account_code=cash_code, account_name=cash_name, debit=0, credit=amount))
    else:
        legs.append(dict(account_code=AR_ACCOUNT[0], account_name=AR_ACCOUNT[1], debit=0, credit=amount))
    return legs


def compose_year_closing_entries(year):
    """年度結轉(2026-09-23,正式三表):跟其他 compose_* 不同,這個直接回頭查總帳本身——
    抓某個西曆年裡代碼開頭 4/5/6/7(收入/成本/費用類)的所有分錄,依科目彙總出借貸淨額,
    對每個有餘額的科目貼一筆方向相反的沖銷腿(全部歸零),淨額(收入類貸餘 − 費用類借餘 =
    本期損益)轉入「累積盈虧」。呼叫前由 main.py 檢查這一年是否已經結轉過(source_id=year),
    這裡不重複檢查。"""
    lo, hi = f"{year}-01-01", f"{year}-12-31"
    rows = q("""SELECT account_code, account_name, SUM(debit) db, SUM(credit) cr
                FROM ledger_entry
                WHERE entry_date BETWEEN ? AND ?
                  AND (account_code LIKE '4%' OR account_code LIKE '5%'
                       OR account_code LIKE '6%' OR account_code LIKE '7%')
                  AND source_type != 'year_closing'
                GROUP BY account_code, account_name
                HAVING db <> cr""", (lo, hi))
    legs, net = [], 0.0
    for r in rows:
        bal = (r["db"] or 0) - (r["cr"] or 0)   # 正=借餘(費用類正常餘額),負=貸餘(收入類正常餘額)
        if bal > 0:
            legs.append(dict(account_code=r["account_code"], account_name=r["account_name"], debit=0, credit=bal))
        else:
            legs.append(dict(account_code=r["account_code"], account_name=r["account_name"], debit=-bal, credit=0))
        net -= bal   # 貸餘(收入)讓淨利增加,借餘(費用)讓淨利減少
    if not legs:
        return []
    if net >= 0:
        legs.append(dict(account_code=EQUITY_RETAINED_ACCOUNT[0], account_name=EQUITY_RETAINED_ACCOUNT[1],
                          debit=0, credit=net))
    else:
        legs.append(dict(account_code=EQUITY_RETAINED_ACCOUNT[0], account_name=EQUITY_RETAINED_ACCOUNT[1],
                          debit=-net, credit=0))
    return legs


def all_accounts():
    """「帳務調整」下拉用:會計科目表裡所有啟用、可以記帳的科目,(代碼, 名稱) 依代碼排序。"""
    return [(r["code"], r["name"]) for r in chart.accounts(postable_only=True)]


def named_legs(legs):
    """分錄的科目名稱一律以會計科目表為準(科目表沒有的代碼保留原名)。"""
    names = {r["code"]: r["name"] for r in q("SELECT code, name FROM account")}
    for leg in legs:
        leg["account_name"] = names.get(leg["account_code"], leg["account_name"])
    return legs


def next_voucher_no(date):
    """傳票號碼一律 GL + 傳票日期 YYYYMMDD + 當天 3 碼流水號(例 GL20260921001,共 13 碼),
    不分來源類別,同一天所有傳票共用一組流水號。"""
    ymd = date.replace("-", "")
    rows = q("SELECT MAX(CAST(SUBSTR(voucher_no, 11) AS INTEGER)) m FROM ledger_entry WHERE voucher_no LIKE ?",
             (f"GL{ymd}%",))
    n = (rows[0]["m"] or 0) + 1
    return f"GL{ymd}{n:03d}"


def save_voucher(source_type, source_id, date, legs, note=None):
    """整張傳票重開:先刪掉這個來源既有的分錄列,再依 legs 插入新的一組。
    legs 為空(例:設備採購)時只清空、不產生新傳票。"""
    execute("DELETE FROM ledger_entry WHERE source_type=? AND source_id=?", (source_type, source_id))
    if not legs:
        return None
    voucher_no = next_voucher_no(date)
    for leg in named_legs(legs):
        execute("""INSERT INTO ledger_entry(voucher_no, entry_date, account_code, account_name,
                     debit, credit, source_type, source_id, note)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (voucher_no, date, leg["account_code"], leg["account_name"],
                 leg["debit"], leg["credit"], source_type, source_id, note))
    return voucher_no


def save_voucher_if_new(source_type, source_id, date, legs, note=None):
    """累加式記錄(不是整張重開):給折舊這種「同一個來源、很多筆歷史分錄」的情境用——
    這個來源 + 這個日期已經記過就跳過,不存在才補插入一筆。過去記過的月份不會被動到。"""
    if not legs:
        return None
    existing = q("SELECT 1 FROM ledger_entry WHERE source_type=? AND source_id=? AND entry_date=? LIMIT 1",
                 (source_type, source_id, date))
    if existing:
        return None
    voucher_no = next_voucher_no(date)
    for leg in named_legs(legs):
        execute("""INSERT INTO ledger_entry(voucher_no, entry_date, account_code, account_name,
                     debit, credit, source_type, source_id, note)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (voucher_no, date, leg["account_code"], leg["account_name"],
                 leg["debit"], leg["credit"], source_type, source_id, note))
    return voucher_no


def delete_voucher_for(source_type, source_id):
    execute("DELETE FROM ledger_entry WHERE source_type=? AND source_id=?", (source_type, source_id))
