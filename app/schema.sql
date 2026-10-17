-- 桂圓帳房 · 資料庫結構 v2  (SQLite)
-- 產季 = 當年 4 月初 ~ 隔年 3 月底,以起始年命名;一列原則上 = 一張訂單(不合併)
PRAGMA foreign_keys = ON;

-- 0. 產品線:事業別 → 產品群組 → SKU(product) -------------------------
--    龍眼(自有果園)底下有 龍眼鮮果 / 龍眼乾 / 龍眼肉;蜂蜜(自養蜂)底下有 蜂蜜。
--    成本大多歸在「產品群組」這一層;稅別(應稅/免稅)也放這層。
CREATE TABLE business_unit (
  bu_id   INTEGER PRIMARY KEY,
  name    TEXT    NOT NULL UNIQUE,        -- 龍眼 / 蜂蜜
  sort    INTEGER NOT NULL DEFAULT 0,
  note    TEXT
);

CREATE TABLE product_group (
  pg_id     INTEGER PRIMARY KEY,
  bu_id     INTEGER NOT NULL REFERENCES business_unit(bu_id),
  name      TEXT    NOT NULL,             -- 龍眼鮮果 / 龍眼乾 / 龍眼肉 / 蜂蜜
  tax_class TEXT    NOT NULL DEFAULT '待確認'
            CHECK (tax_class IN ('待確認','應稅','免稅','零稅率')),
  sort      INTEGER NOT NULL DEFAULT 0,
  note      TEXT,
  UNIQUE (bu_id, name)
);

-- 1. 商品目錄(= SKU / 品項) ------------------------------------------
CREATE TABLE product (
  product_id      INTEGER PRIMARY KEY,
  sku             TEXT    NOT NULL UNIQUE,
  name            TEXT    NOT NULL,
  product_type    TEXT,                 -- 單品 / 組合 / 禮盒 / 裸裝 / 試吃包 / 加購贈品
  type_code_raw   TEXT,                 -- 對方原代號,如 #24 #26-300g
  net_weight_g    INTEGER,              -- 內容量(克);秤重品留 NULL
  gross_weight_g  INTEGER,
  package_form    TEXT,                 -- 夾鏈袋 / 罐 / 禮盒 / 真空袋 / 裸裝
  uom             TEXT    NOT NULL DEFAULT '包',   -- 包 / 斤 / 盒 / 罐 / 支
  grams_per_uom   INTEGER,              -- 1 計價單位 = 幾克(換算用)
  shelf_life_days INTEGER,
  storage_condition TEXT DEFAULT '常溫',            -- 常溫 / 冷藏 / 冷凍
  ingredients     TEXT,
  origin          TEXT DEFAULT '南投中寮',
  allergen        TEXT,
  barcode         TEXT,
  gift_only       INTEGER NOT NULL DEFAULT 0 CHECK (gift_only IN (0,1)),
  status          TEXT    NOT NULL DEFAULT '在售' CHECK (status IN ('在售','停售')),
  product_group_id INTEGER REFERENCES product_group(pg_id),   -- 歸屬產品群組;NULL = 尚未歸類
  unit_cost       REAL    NOT NULL DEFAULT 0,   -- 每單位成本(算毛利用,不分批次)
  note            TEXT
);

-- 2. 定價表 ------------------------------------------------------------
CREATE TABLE price_list (
  price_id        INTEGER PRIMARY KEY,
  product_id      INTEGER NOT NULL REFERENCES product(product_id),
  customer_segment TEXT   NOT NULL CHECK (customer_segment IN ('零售','批發','團購','機構','內部')),
  channel_id      INTEGER REFERENCES channel(channel_id),   -- NULL = 通用
  unit_price      REAL    NOT NULL,
  effective_from  TEXT    NOT NULL DEFAULT '2000-01-01',
  effective_to    TEXT,
  note            TEXT
);
CREATE INDEX ix_price_lookup ON price_list(product_id, customer_segment, effective_from);

-- 3. 客戶主檔 ------------------------------------------------------------
CREATE TABLE customer (
  customer_id     INTEGER PRIMARY KEY,
  cust_no         TEXT    UNIQUE,               -- 客戶編號 C + 年 + 3 碼(例 C2026001,2026-10-16 依 Excel)
  display_name    TEXT    NOT NULL,
  customer_type   TEXT,                         -- 客戶分類:企業戶 / 個人戶(2026-10-16 起;舊資料可能是其他字)
  segment         TEXT,                         -- 舊的客戶分級,2026-10-16 起不分級,欄位保留不用
  primary_channel_id INTEGER REFERENCES channel(channel_id),
  phone           TEXT,
  email           TEXT,
  contact_person  TEXT,
  invoice_title   TEXT,
  tax_id          TEXT,                 -- 統一編號
  tax_doc_pref    TEXT CHECK (tax_doc_pref IN ('電子發票二聯','電子發票三聯','農民收據','免開立')),
  first_order_date TEXT,
  tags            TEXT,
  note            TEXT
);

-- 3b. 客戶別名對照 ----------------------------------------------------
CREATE TABLE customer_alias (
  alias_id        INTEGER PRIMARY KEY,
  customer_id     INTEGER NOT NULL REFERENCES customer(customer_id),
  alias_text      TEXT    NOT NULL UNIQUE
);

-- 4. 收件地址 --------------------------------------------------------
CREATE TABLE address (
  address_id      INTEGER PRIMARY KEY,
  customer_id     INTEGER NOT NULL REFERENCES customer(customer_id),
  label           TEXT,                 -- 自宅 / 公司 / 其他
  recipient_name  TEXT,
  recipient_phone TEXT,
  zipcode         TEXT,
  address_full    TEXT,
  is_default      INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0,1))
);

-- 5. 通路 ----------------------------------------------------------
CREATE TABLE channel (
  channel_id      INTEGER PRIMARY KEY,
  code            TEXT    NOT NULL UNIQUE,
  name            TEXT    NOT NULL,
  category        TEXT,                 -- 官網 / LINE社群 / 電商平台 / 超商賣貨便 / 實體寄售 / 市集展售 / 媒體導流 / 批發 / 直售
  commission_pct  REAL    NOT NULL DEFAULT 0,
  settlement_lag_days INTEGER NOT NULL DEFAULT 0,
  note            TEXT
);

-- 6. 生產批次 -----------------------------------------------------
CREATE TABLE batch (
  batch_id        INTEGER PRIMARY KEY,
  batch_code      TEXT    NOT NULL UNIQUE,
  season          INTEGER NOT NULL,     -- 產季年
  product_id      INTEGER REFERENCES product(product_id),   -- 主要對應商品(可 NULL=多品)
  roast_start     TEXT,
  roast_end       TEXT,
  raw_source      TEXT,
  raw_input_kg    REAL,
  output_qty      REAL,                 -- 產出成品量
  output_uom      TEXT DEFAULT '包',
  mfg_date        TEXT,                 -- 製造日(標示 + 效期基準)
  unit_cost       REAL,                 -- 單位製造成本
  note            TEXT
);

-- 7. 訂單 --------------------------------------------------------
CREATE TABLE "order" (
  order_id        INTEGER PRIMARY KEY,
  order_no        TEXT    NOT NULL UNIQUE,
  order_date      TEXT    NOT NULL,
  season          INTEGER NOT NULL,     -- 產季
  customer_id     INTEGER REFERENCES customer(customer_id),
  channel_id      INTEGER REFERENCES channel(channel_id),
  order_kind      TEXT    NOT NULL DEFAULT '銷售'
                  CHECK (order_kind IN ('銷售','贈送-公關','贈送-捐贈','樣品','理賠重寄','換貨補出','內部領用')),
  source_ref      TEXT,                 -- 活動/媒體來源
  discount_total  REAL    NOT NULL DEFAULT 0,
  shipping_fee_charged REAL NOT NULL DEFAULT 0,   -- 向客戶收取的運費(收入面)
  platform_fee    REAL    NOT NULL DEFAULT 0,     -- 通路抽成金額
  order_total     REAL    NOT NULL DEFAULT 0,     -- 應收 = 小計 - 折讓 + 向客收運費
  sales_tax       REAL    NOT NULL DEFAULT 0,     -- 銷項稅額(2026-10-08:售價含稅,家易手動填,系統不算)
  cost_payment_account TEXT,                      -- 成本付給小農的帳戶(2026-10-16:接單當天就付,C 組名稱)
  payment_method  TEXT    CHECK (payment_method IN ('現金','銀行匯款','貨到付款','行動支付','信用卡','平台代收','未收款')),
  payment_account TEXT,                 -- 郵局 / 合庫 …
  payment_status  TEXT    NOT NULL DEFAULT '待收款'
                  CHECK (payment_status IN ('待收款','部分收款','已收款','免收款')),
  paid_date       TEXT,
  paid_amount     REAL,
  tax_doc_type    TEXT CHECK (tax_doc_type IN ('電子發票二聯','電子發票三聯','農民收據','免開立')),  -- 舊欄位,畫面已不用
  invoiced        INTEGER NOT NULL DEFAULT 0 CHECK (invoiced IN (0,1)),  -- 這筆有沒有開發票(單純記錄,不算稅)
  tax_doc_no      TEXT,                 -- 發票號碼(選填,已開發票才有意義)
  -- 物流
  ship_method     TEXT CHECK (ship_method IN ('自行配送','客戶自取','宅配','超商店到店','超商賣貨便','冷藏宅配')),
  carrier         TEXT,
  tracking_no     TEXT,
  ship_from       TEXT,
  shipped_date    TEXT,
  delivered_date  TEXT,
  shipping_cost_actual REAL NOT NULL DEFAULT 0,   -- 我方實付運費(費用面)
  ship_payer      TEXT NOT NULL DEFAULT '店家吸收' CHECK (ship_payer IN ('店家吸收','客戶付')),
                  -- 2026-10-03:'客戶付' 時 order_total/應收帳款含運費(客人要付的錢比訂單金額高),
                  -- 總帳運費科目借貸互相沖平,對公司損益淨影響是 0(見 ledger.compose_order_sale_entries)
  ship_status     TEXT NOT NULL DEFAULT '待出貨'
                  CHECK (ship_status IN ('待出貨','已出貨','已送達','退回','遺失','破損')),
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_order_date   ON "order"(order_date);
CREATE INDEX ix_order_season ON "order"(season);
CREATE INDEX ix_order_cust   ON "order"(customer_id);

-- 8. 訂單明細 ---------------------------------------------------
CREATE TABLE order_line (
  line_id         INTEGER PRIMARY KEY,
  order_id        INTEGER NOT NULL REFERENCES "order"(order_id) ON DELETE CASCADE,
  product_id      INTEGER NOT NULL REFERENCES product(product_id),
  batch_id        INTEGER REFERENCES batch(batch_id),
  qty             REAL    NOT NULL,
  unit_price      REAL    NOT NULL DEFAULT 0,     -- 成交單價
  list_price      REAL,                           -- 當下標準價(帶入自 price_list)
  line_discount   REAL    NOT NULL DEFAULT 0,
  line_subtotal   REAL    NOT NULL DEFAULT 0,     -- qty*unit_price - line_discount
  line_cost       REAL NOT NULL DEFAULT 0,        -- 這一列的成本(2026-10-16:家易每張訂單自己填,系統不算;
                                                  -- 記「借 採購成本(依產品線)/ 貸 應付帳款」,也是毛利的成本)
  unit_cost       REAL,                           -- 舊欄位:開單當下的商品單位成本(2026-10-08),2026-10-16 起不用;
                                                  -- 舊訂單照當初的成本算,欠小農的金額不會跟著變;NULL 才退回用商品目錄的
  is_gift         INTEGER NOT NULL DEFAULT 0 CHECK (is_gift IN (0,1)),
  expiry_stated   TEXT                            -- 歷史「保存期限」原值,保留欄
);
CREATE INDEX ix_line_order   ON order_line(order_id);
CREATE INDEX ix_line_product ON order_line(product_id);

-- 9. 物流異常 / 退補 -----------------------------------------
CREATE TABLE shipment_issue (
  issue_id        INTEGER PRIMARY KEY,
  order_id        INTEGER NOT NULL REFERENCES "order"(order_id),
  issue_type      TEXT NOT NULL CHECK (issue_type IN ('遺失','破損','退貨','客訴','到期回收')),
  issue_date      TEXT NOT NULL,
  qty_affected    REAL,
  resolution      TEXT CHECK (resolution IN ('重寄','退款','換貨','折讓','無')),
  linked_reship_order_id INTEGER REFERENCES "order"(order_id),
  cost_impact     REAL NOT NULL DEFAULT 0,
  reason_note     TEXT
);

-- 10. 產季目標 --------------------------------------------
CREATE TABLE sales_target (
  target_id       INTEGER PRIMARY KEY,
  season          INTEGER NOT NULL,
  product_id      INTEGER REFERENCES product(product_id),   -- NULL = 整體
  target_qty      REAL,
  target_amount   REAL,
  UNIQUE (season, product_id)
);

-- 11. 待確認佇列 -----------------------------------------
CREATE TABLE review_queue (
  review_id       INTEGER PRIMARY KEY,
  source          TEXT NOT NULL DEFAULT 'form' CHECK (source IN ('import','form')),
  raw_json        TEXT,
  issue_type      TEXT,
  status          TEXT NOT NULL DEFAULT '待處理' CHECK (status IN ('待處理','已修','已忽略')),
  resolved_order_id INTEGER REFERENCES "order"(order_id),
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 12. 營運費用(月)(v2 回合 2b:加產品線標記 + 攤提 + 報稅欄) ----
CREATE TABLE op_expense (
  expense_id  INTEGER PRIMARY KEY,
  ym          TEXT NOT NULL,          -- YYYY-MM(由 expense_date 推;舊資料攤提時 = 起始月)
  expense_date TEXT,                  -- 交易日期(2026-10-17:同仁意見「日期就是日期」,傳票日期用這個)
  category    TEXT NOT NULL,          -- 直接人工 / 田間管理 / 驗證費 / 研發 / 人事 / 場地・倉儲 / 行銷 / 金流手續費 / 設備維護 / 培訓 / 其他
  account_code  TEXT,                 -- 2026-10-16:費用科目(VD 營運費用 / VE 其他費用),category 存科目名稱
  amount      REAL NOT NULL DEFAULT 0,       -- 帳載值(實際花的錢;B 層報稅看這個)
  product_group_id INTEGER REFERENCES product_group(pg_id),  -- 歸哪條產品線;NULL = 共同
  amortize_months  INTEGER,           -- 攤提月數;NULL / 1 = 當月全額;N = 從 ym 起每月 amount/N(管理視角)
  tax_amount       REAL NOT NULL DEFAULT 0,  -- 進項稅額
  tax_deductible   INTEGER NOT NULL DEFAULT 0 CHECK (tax_deductible IN (0,1)),
  doc_type        TEXT,                 -- 憑證類型:發票 / 收據(2026-10-16 依 Excel)
  invoice_no       TEXT,                -- 發票號碼(憑證類型選發票才需要填)
  payment_account  TEXT,               -- 用哪個帳戶付款(自由文字,例:現金 / 合庫);方案B分錄用
  note        TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))   -- 最後新增 / 修改時間
);
CREATE INDEX ix_opexp_ym ON op_expense(ym);

-- 16. 固定資產卡(v2 回合 3) --------------------------------
--     平均法折舊:每年 = (成本 − 補助 − 殘值) ÷ 耐用年數;每月 = 年 ÷ 12。
--     折舊費用由 queries.opex_rows() 動態算入「折舊」類別,不存成 op_expense 列。
CREATE TABLE fixed_asset (
  asset_id     INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,                    -- 柴焙灶 / 剝殼機 / 搖蜜機 …
  category     TEXT CHECK (category IN ('機器設備','生財器具','運輸設備','電腦設備','房屋建築','其他')),
  product_group_id INTEGER REFERENCES product_group(pg_id),   -- 歸哪條產品線;NULL = 共同
  acquire_date TEXT NOT NULL,                    -- 取得日 YYYY-MM-DD
  cost         REAL NOT NULL DEFAULT 0,          -- 取得成本
  grant_amount REAL NOT NULL DEFAULT 0,          -- 政府補助款(補助部分不提折舊)
  salvage      REAL NOT NULL DEFAULT 0,          -- 殘值(平均法稅法建議 = 成本 ÷ (年數+1))
  life_years   INTEGER NOT NULL DEFAULT 10,      -- 舊欄位(年);2026-10-16 起看 life_months
  life_months  INTEGER,                          -- 使用年限(月),最少 120;每月折舊 = 無條件進位(成本 ÷ 月數)
  tax_amount   REAL NOT NULL DEFAULT 0,          -- 買設備的進項稅額
  method       TEXT NOT NULL DEFAULT '平均法',
  disposed_date TEXT,                            -- 處分日;NULL = 在用
  payment_account TEXT,               -- 用哪個帳戶付款(自由文字,例:現金 / 合庫);總帳分錄用
  note         TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 17. 定價試算參數(單一 key-value;預設值定義在 queries.PRICING_FIELDS) --
--     這是「管理估算」用,不進帳本;見 docs/定價分析.md。
CREATE TABLE pricing_param (
  key   TEXT PRIMARY KEY,
  value REAL NOT NULL
);

-- 18. 訂單退款 / 退貨(2026-10-08 貿易公司模式,取代原本的銷貨退回 / 折讓) ----
--     退貨又退款 → 直接刪訂單,不記在這裡。
--     kind='退款':只退款不退貨,一律算其他支出(6499 其他損失)。
--     kind='退回小農':退貨但不退款,貨退給小農、小農退成本(借 應付帳款 / 貸 採購成本)。
CREATE TABLE sales_return (
  return_id    INTEGER PRIMARY KEY,
  order_id     INTEGER NOT NULL REFERENCES "order"(order_id) ON DELETE CASCADE,
  return_date  TEXT NOT NULL,                    -- 發生日(認列期間看這個)
  season       INTEGER NOT NULL,                 -- 由 return_date 推
  kind         TEXT NOT NULL DEFAULT '退款' CHECK (kind IN ('退款','退回小農')),
  amount       REAL NOT NULL DEFAULT 0,          -- 退款:退給客人的錢;退回小農:小農退回的錢(正數)
  payment_account TEXT,                          -- 2026-10-16:退款時錢從哪個帳戶退出(C 組)
  cost_account TEXT,                             -- 2026-10-16:退回小農時沖哪個採購成本(VA 組)
  reason       TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_sret_order ON sales_return(order_id);
CREATE INDEX ix_sret_date  ON sales_return(return_date);
CREATE INDEX ix_sret_season ON sales_return(season);

-- 18b. 進口商品(2026-10-16;同一天稍晚改成 dormant)--------------------------
--     使用者決定「接單當天就付錢給小農」(Toyota 無存貨的概念),付款直接在訂單成立的傳票裡一起記,
--     這張表跟 /goods-purchases 頁面都不用了,表留著不刪。
--     同事「會計場景對應分錄表_1015」場景 2「進口商品:實際付款」= 付錢給小農:
--     借 2171 應付帳款 / 貸 現金或銀行。成本在開訂單時記(借 採購成本 / 貸 應付帳款)。
CREATE TABLE goods_purchase (
  purchase_id     INTEGER PRIMARY KEY,
  purchase_date   TEXT NOT NULL,
  cost_account    TEXT,               -- 舊欄位(不用):2026-10-16 起進口商品 = 付錢給小農,不分產品線
  doc_type        TEXT NOT NULL DEFAULT '收據' CHECK (doc_type IN ('發票','收據')),
  invoice_no      TEXT,               -- 憑證類型是發票才要填
  amount          REAL NOT NULL DEFAULT 0,   -- 整數
  payment_account TEXT,               -- C 組帳戶名稱(現金 / 合庫 …)
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 19. 總帳明細(方案 B 試點:進貨 / 營運費用 自動過帳,2026-09) ----
--     家易照舊填業務表單,系統依 ledger.py 的對照表組出分錄,存檔前在確認畫面
--     給他看、可微調。同一來源(進貨/費用)重存 = 整張傳票重開(先刪舊列再插新列),
--     不做部分修改 / 部分刪除。這次只接「進貨」「原料/包材/委外/服務/其他類」與
--     「營運費用」——設備採購走固定資產,不在這批範圍。
CREATE TABLE ledger_entry (
  entry_id     INTEGER PRIMARY KEY,
  voucher_no   TEXT NOT NULL,          -- 同一張傳票共用,例:GL20260917001(GL+日期+當天流水號)
  entry_date   TEXT NOT NULL,
  account_code TEXT NOT NULL,
  account_name TEXT NOT NULL,
  debit        REAL NOT NULL DEFAULT 0,
  credit       REAL NOT NULL DEFAULT 0,
  source_type  TEXT NOT NULL CHECK (source_type IN ('op_expense','order_sale','order_payment',
                 'goods_purchase','other_income','equity','manual_adjustment',
                 'asset_acquire','asset_depreciation','sales_return','year_closing')),
  source_id    INTEGER NOT NULL,       -- 對應來源資料表的主鍵(例:op_expense.expense_id)
  note         TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_ledger_voucher ON ledger_entry(voucher_no);
CREATE INDEX ix_ledger_source  ON ledger_entry(source_type, source_id);

-- 20. 其他收益(9 宮格 B 類,2026-09) ---------------------------------
--     不屬於訂單銷售的現金收入,例:利息收入、政府補助收入。
CREATE TABLE other_income (
  income_id       INTEGER PRIMARY KEY,
  income_date     TEXT NOT NULL,
  category        TEXT NOT NULL,          -- 收入科目名稱(2026-10-16 起 = UB 組科目)
  account_code    TEXT,                   -- UB 組科目代碼,例 7101
  doc_type        TEXT,                   -- 發票 / 收據
  invoice_no      TEXT,                   -- 憑證類型是發票才要填
  amount          REAL NOT NULL DEFAULT 0,
  tax_amount      REAL NOT NULL DEFAULT 0,   -- 銷項稅額(通常沒有,留 0)
  payment_account TEXT,                    -- 用哪個帳戶收款(自由文字,例:現金 / 合庫)
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_oinc_date ON other_income(income_date);

-- 21. 資本異動(9 宮格 B 類,2026-09) ---------------------------------
CREATE TABLE equity_txn (
  txn_id          INTEGER PRIMARY KEY,
  txn_date        TEXT NOT NULL,
  txn_type        TEXT NOT NULL CHECK (txn_type IN ('現金增資','盈餘轉列公積')),
  amount          REAL NOT NULL DEFAULT 0,
  payment_account TEXT,                    -- 現金增資才有意義
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_equity_date ON equity_txn(txn_date);

-- 22. 帳務調整(9 宮格 B 類,2026-09) ---------------------------------
--     其他 8 類都涵蓋不到的例外狀況,直接指定一組借/貸科目手動記一筆。
CREATE TABLE manual_entry (
  entry_id     INTEGER PRIMARY KEY,
  entry_date   TEXT NOT NULL,
  debit_code   TEXT NOT NULL,
  debit_name   TEXT NOT NULL,
  credit_code  TEXT NOT NULL,
  credit_name  TEXT NOT NULL,
  amount       REAL NOT NULL DEFAULT 0,
  note         TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_manual_date ON manual_entry(entry_date);

-- 23. 會計科目表(2026-10-16,取代原本的 bank_account)------------------------
--     依同事「郡碩農創_記帳系統_1015.xlsx」帳務設定表建立,初值在 chart.DEFAULT_CHART。
--     grp = 帳務設定表的分組字母(C 現金銀行 / UA 銷貨收入 / VD 營運費用 …),各記帳畫面
--     的下拉選單只列自己用得到的組。「會計科目設定」頁(/accounts)可以新增、改名、停用;
--     改名會連總帳裡已經記過的科目名稱一起改。銀行帳戶 = C 組裡 1103-NN 的科目,付款帳戶
--     打一個新名字時 chart.cash_account() 會自動接著編號新增。
CREATE TABLE account (
  code       TEXT PRIMARY KEY,           -- 例:1103-01、4111-02
  name       TEXT NOT NULL,              -- 例:合庫、銷貨收入-龍眼乾
  grp        TEXT NOT NULL,              -- C / E / F / G / J / K / N / P / T / UA / VA / VC / VD / UB / VE / 其他
  sort       INTEGER NOT NULL DEFAULT 0,
  active     INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),   -- 0 = 停用(下拉選單不出現,舊傳票照常顯示)
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 24. 全鏈路毛利分析(2026-10,管理視角估算,不進總帳/不影響報稅)----------
--     供應端生產成本是「整季估算總額」,不是單價。2026-10-02 定案:四條產品線不分
--     「公司自己加工 vs 跟供應端買現成加工品」,也不特定是外部小農——郡碩沒有自有
--     果園,但老闆(家易)自己有果園,郡碩是跟他買鮮果或加工品,買了鮮果可能自己加工
--     再賣、也可能買加工品直接包裝賣,同一條產品線每季供應方式都可能不同,系統沒辦法
--     判斷、也不需要判斷——只用一個不預設情境的欄位,讓家易自己估「這筆採購款裡面
--     大概多少算供應端自己的成本」就好,不拆成田間管理/採收工資兩類。依產季各存一組,
--     不是只有一個「現在最新值」,查不到當季資料時由 queries.chain_cost_params() 往回
--     找最近一季的值當預設,不在這裡自動補列。
CREATE TABLE chain_cost_param (
  season        INTEGER NOT NULL,
  pg_name       TEXT NOT NULL,      -- 龍眼鮮果/龍眼乾/龍眼肉/蜂蜜,跟 ledger.COGS_INVENTORY_ACCOUNTS 同一組字串
  supply_cost   REAL NOT NULL DEFAULT 0,   -- 供應端生產成本估算(整季總額)
  note          TEXT,
  updated_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (season, pg_name)
);
