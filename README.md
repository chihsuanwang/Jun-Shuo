# 郡碩農創 · 桂圓帳房(Jun-Shuo)

南投中寮 **郡碩農創有限公司** 的本機營運管理平台。
自有龍眼果園 + 自養蜂,銷售龍眼(鮮果 / 桂圓乾 / 龍眼肉)與蜂蜜產品。
(公司另有設備 / 授權 / 服務等商業計畫,屬未來範圍;現階段系統只做產品銷售 —— 見 `app/docs/開發路線.md`。)

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

### Claude Code 用的 Skills

這些是裝在 Windows 帳號個人目錄(`C:\Users\<你>\.agents\skills\`)的 Claude Code 工具設定,
**不隨 git clone 帶過去**,換電腦要另外裝。這裡只記錄「有裝什麼、怎麼裝」,方便兩台電腦對齊,
不是每次都要真的裝——只有真的要用某個功能時才裝那一個。

| Skill | 用途 | 怎麼裝 |
|---|---|---|
| **agent-browser** | 瀏覽器自動化(dogfood 探索式測試、走位截圖、示意圖 Artifact 發布前預覽)—— 這個專案主要在用的就是這個 | `npm i -g agent-browser && agent-browser install`(會另外裝 Chrome,~50MB,每台電腦分開裝) |
| find-skills | 幫忙找/裝其他 skill 的 meta skill | 通常內建或用它裝別的 skill 時順便有 |
| pdf | PDF 讀取 / 合併 / 填表等操作 | 目前這個專案還沒實際用過 |

## 打包成免安裝 exe(給郡碩)

`app` 目錄下跑 **`打包.bat`**(需先跑過一次 `啟動.bat` 建好 `.venv`)→ 產生 `app/dist/桂圓帳房/`。
把整個 `桂圓帳房` 資料夾(壓縮後)交付,對方雙擊 `桂圓帳房.exe` 即可用,不需 Python。
使用方式見 [app/docs/交付說明.md](app/docs/交付說明.md)。exe / build 產物不進版控。

---

## 文件(`app/docs/`)

| 檔案 | 內容 |
|---|---|
| **開發路線.md** | ★ 現在做到哪、決定了什麼、下一步 —— **要繼續施工先看這份** |
| **工作日誌.md** | ★ 每次施工細項 + 待辦 / 決策清單 —— **換電腦接手先看這份** |
| **定價分析.md** | 單位成本往上堆疊 → 建議售價;判斷哪條產品線該擴 / 該收(非帳本,估算用) |
| **臨時分享.md** | 用 cloudflared 開臨時公開網址,短時間讓同仁連進來試用(假資料用) |
| **上雲-pythonanywhere.md** | 部署到 PythonAnywhere 免費版:固定網址、共用密碼,同仁隨時連(假資料試用) |
| **展示腳本.md** | demo 給郡碩前自己先跑一遍的逐步走位 + 對數字 + 提問清單 |
| **交付說明.md** | 給郡碩:免安裝 exe 怎麼用、資料在哪、怎麼備份 / 搬電腦 |
| **報稅參考.md** | 郡碩的 403 / 營所稅申報現況、留抵稅額、待釐清事項 |
| 系統文件.md | 資料庫結構、路由、關鍵邏輯 |
| 使用手冊.md | 給操作者(郡碩)的操作說明 |
| 儀表板解讀.md | 每個數字怎麼看 |
| 平台概觀.md | 路演 / 對外簡介 |

---

## 版控慣例

- **每個施工回合** = 功能 → 冒煙測試(全路由 200)→ commit,**直接在 `main` 上做**,不另外開
  feature branch、不必每回合都推。
- commit 訊息用中文,說清楚「這回合動了什麼、沒動什麼」。
- **`git push` 等使用者明確說「推上 GitHub」才推**——先在本機做到一個段落,累積幾個 commit
  再一次推,不是每個 commit 都要推。使用者自己另外有整個專案資料夾的本機備份當保險,
  不靠 git branch 隔離風險。
- `app/備份/` 另有每日資料庫自動備份(啟動時)。
- 分支 `main`;遠端 `origin` = <https://github.com/chihsuanwang/Jun-Shuo>
