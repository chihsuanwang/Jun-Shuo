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
  low_stock       INTEGER,              -- 低庫存警戒量;NULL = 不設
  product_group_id INTEGER REFERENCES product_group(pg_id),   -- 歸屬產品群組;NULL = 尚未歸類
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
  display_name    TEXT    NOT NULL,
  customer_type   TEXT    CHECK (customer_type IN ('個人','公司','機構團體','通路商','內部')),
  segment         TEXT    CHECK (segment IN ('零售','批發','團購主','機構','公關對象')),
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
  payment_method  TEXT    CHECK (payment_method IN ('現金','銀行匯款','貨到付款','行動支付','信用卡','平台代收','未收款')),
  payment_account TEXT,                 -- 郵局 / 合庫 …
  payment_status  TEXT    NOT NULL DEFAULT '待收款'
                  CHECK (payment_status IN ('待收款','部分收款','已收款','免收款')),
  paid_date       TEXT,
  paid_amount     REAL,
  tax_doc_type    TEXT CHECK (tax_doc_type IN ('電子發票二聯','電子發票三聯','農民收據','免開立')),
  tax_doc_no      TEXT,
  -- 物流
  ship_method     TEXT CHECK (ship_method IN ('自行配送','客戶自取','宅配','超商店到店','超商賣貨便','冷藏宅配')),
  carrier         TEXT,
  tracking_no     TEXT,
  ship_from       TEXT,
  shipped_date    TEXT,
  delivered_date  TEXT,
  shipping_cost_actual REAL NOT NULL DEFAULT 0,   -- 我方實付運費(費用面)
  packaging_cost  REAL NOT NULL DEFAULT 0,
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
  ym          TEXT NOT NULL,          -- YYYY-MM(攤提時 = 起始月)
  category    TEXT NOT NULL,          -- 直接人工 / 田間管理 / 驗證費 / 研發 / 人事 / 場地・倉儲 / 行銷 / 金流手續費 / 設備維護 / 培訓 / 其他
  amount      REAL NOT NULL DEFAULT 0,       -- 帳載值(實際花的錢;B 層報稅看這個)
  product_group_id INTEGER REFERENCES product_group(pg_id),  -- 歸哪條產品線;NULL = 共同
  amortize_months  INTEGER,           -- 攤提月數;NULL / 1 = 當月全額;N = 從 ym 起每月 amount/N(管理視角)
  tax_amount       REAL NOT NULL DEFAULT 0,  -- 進項稅額
  tax_deductible   INTEGER NOT NULL DEFAULT 0 CHECK (tax_deductible IN (0,1)),
  doc_type         TEXT CHECK (doc_type IN ('三聯式發票','二聯式發票','收據','農民收據','無憑證')),
  note        TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))   -- 最後新增 / 修改時間
);
CREATE INDEX ix_opexp_ym ON op_expense(ym);

-- 13. 庫存異動(SKU 層,可追溯批次) ---------------------
CREATE TABLE stock_move (
  move_id      INTEGER PRIMARY KEY,
  move_date    TEXT NOT NULL DEFAULT (date('now','localtime')),
  product_id   INTEGER NOT NULL REFERENCES product(product_id),
  batch_id     INTEGER REFERENCES batch(batch_id),
  qty          REAL NOT NULL,            -- 正 = 入庫,負 = 出庫
  move_type    TEXT NOT NULL,            -- 期初庫存 / 分裝入庫 / 銷售出庫 / 贈送出庫 / 盤點調整 / 損耗報廢 / 退貨入庫
  ref_order_id INTEGER REFERENCES "order"(order_id),
  note         TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_move_product ON stock_move(product_id);
CREATE INDEX ix_move_order   ON stock_move(ref_order_id);

-- 14. 供應商主檔(v2 回合 2) ---------------------------
CREATE TABLE supplier (
  supplier_id INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  tax_id      TEXT,                    -- 統一編號
  category    TEXT CHECK (category IN ('原料','包材','委外加工','設備','服務','其他')),
  phone       TEXT,
  note        TEXT
);

-- 15. 進貨單(買進來的成本:原料 / 包材 / 委外 / 設備)(v2 回合 2) ---
--     只做「登錄」;真正接進產品線損益是回合 6(避免和批次單位成本重複計算)。
CREATE TABLE purchase (
  purchase_id      INTEGER PRIMARY KEY,
  purchase_date    TEXT NOT NULL,                        -- YYYY-MM-DD
  supplier_id      INTEGER REFERENCES supplier(supplier_id),
  category         TEXT CHECK (category IN ('原料','包材','委外加工','設備','服務','其他')),
  product_group_id INTEGER REFERENCES product_group(pg_id),   -- 歸哪條產品線;NULL = 共同
  amount           REAL NOT NULL DEFAULT 0,              -- 未稅金額
  tax_amount       REAL NOT NULL DEFAULT 0,              -- 進項稅額
  tax_deductible   INTEGER NOT NULL DEFAULT 1 CHECK (tax_deductible IN (0,1)),
  doc_type         TEXT CHECK (doc_type IN ('三聯式發票','二聯式發票','收據','農民收據','無憑證')),
  is_fixed_asset   INTEGER NOT NULL DEFAULT 0 CHECK (is_fixed_asset IN (0,1)),  -- 打勾標記;固定資產卡回合 3 才建
  note             TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_purchase_date ON purchase(purchase_date);

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
  life_years   INTEGER NOT NULL DEFAULT 5,       -- 耐用年數
  method       TEXT NOT NULL DEFAULT '平均法',
  source_purchase_id INTEGER REFERENCES purchase(purchase_id),  -- 從哪筆進貨建卡(可空)
  disposed_date TEXT,                            -- 處分日;NULL = 在用
  note         TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 17. 定價試算參數(單一 key-value;預設值定義在 queries.PRICING_FIELDS) --
--     這是「管理估算」用,不進帳本;見 docs/定價分析.md。
CREATE TABLE pricing_param (
  key   TEXT PRIMARY KEY,
  value REAL NOT NULL
);

-- 18. 銷貨退回 / 折讓(v2 回合 8) -----------------------------
--     針對某張「銷售」單,在退貨 / 折讓「發生的期間」沖減營收。
--     restock=1:好貨退回可再賣 → 產生一筆「退貨入庫」;成本也回沖。
--     restock=0:折讓或壞貨報廢 → 只沖營收,成本已沉沒。
CREATE TABLE sales_return (
  return_id    INTEGER PRIMARY KEY,
  order_id     INTEGER NOT NULL REFERENCES "order"(order_id) ON DELETE CASCADE,
  return_date  TEXT NOT NULL,                    -- 退貨 / 折讓發生日(認列期間看這個)
  season       INTEGER NOT NULL,                 -- 由 return_date 推
  kind         TEXT NOT NULL DEFAULT '退貨' CHECK (kind IN ('退貨','折讓')),
  amount       REAL NOT NULL DEFAULT 0,          -- 沖減的營收金額(正數)
  product_id   INTEGER REFERENCES product(product_id),   -- 有退實體貨才填
  qty          REAL,                             -- 退回數量
  batch_id     INTEGER REFERENCES batch(batch_id),
  restock      INTEGER NOT NULL DEFAULT 0 CHECK (restock IN (0,1)),  -- 1=進庫可再賣;0=報廢 / 純折讓
  reason       TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX ix_sret_order ON sales_return(order_id);
CREATE INDEX ix_sret_date  ON sales_return(return_date);
CREATE INDEX ix_sret_season ON sales_return(season);
