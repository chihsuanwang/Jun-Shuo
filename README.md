# 郡碩農創 · 桂圓帳房(Jun-Shuo)

南投中寮 **郡碩農創有限公司** 的本機營運管理平台。
地方創生 / 農業科技公司:自有龍眼果園 + 自養蜂,開發智慧焙製設備與工法,
賣設備 / 授權 / 服務,也賣龍眼(鮮果 / 桂圓乾 / 龍眼肉)與蜂蜜產品。

技術:FastAPI + Jinja2 + SQLite,**純本機、無登入、無雲端**
(客戶姓名 / 電話 / 地址是個資,只留在自己電腦,不進版控)。

---

## 在新電腦上跑起來

需求:Windows + Python 3.11(較新版本也可,但套件要裝在同一個直譯器)。

```bat
git clone https://github.com/chihsuanwang/Jun-Shuo.git
cd Jun-Shuo\app

py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

REM repo 不含資料庫,第一次要自己灌示範資料
.venv\Scripts\python seed.py --force

REM 啟動(或直接雙擊 啟動.bat)
.venv\Scripts\python -m uvicorn main:app --port 8000
```

瀏覽器開 <http://127.0.0.1:8000> 。

> 換電腦只帶程式碼、不帶資料。`*.db` / `*.xlsx` / `*.csv` / `app/備份/` 都在 `.gitignore` 裡。

---

## 文件(`app/docs/`)

| 檔案 | 內容 |
|---|---|
| **開發路線.md** | ★ 現在做到哪、決定了什麼、下一步 —— **要繼續施工先看這份** |
| **報稅參考.md** | 郡碩的 403 / 營所稅申報現況、留抵稅額、待釐清事項 |
| 系統文件.md | 資料庫結構、路由、關鍵邏輯 |
| 使用手冊.md | 給操作者(郡碩)的操作說明 |
| 儀表板解讀.md | 每個數字怎麼看 |
| 平台概觀.md | 路演 / 對外簡介 |

---

## 版控慣例

- **每個施工回合** = 功能 → 冒煙測試(全路由 200)→ commit → `git push`
- commit 訊息用中文,說清楚「這回合動了什麼、沒動什麼」
- 動大結構前先 commit 存檢查點;`app/備份/` 另有每日資料庫自動備份(啟動時)
- 分支 `main`;遠端 `origin` = <https://github.com/chihsuanwang/Jun-Shuo>
