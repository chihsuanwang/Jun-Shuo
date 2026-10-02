# 郡碩農創 · 桂圓帳房(Jun-Shuo)

南投中寮 **郡碩農創有限公司** 的營運管理平台。
郡碩沒有自有果園 / 蜂場,跟小農(含老闆家易自己的果園)採購鮮果或加工品,
銷售龍眼(鮮果 / 桂圓乾 / 龍眼肉)與蜂蜜產品。
(公司另有設備 / 授權 / 服務等商業計畫,屬未來範圍;現階段系統只做產品銷售 —— 見 `app/docs/開發路線.md`。)

技術:FastAPI + Jinja2 + SQLite。預設**本機跑、無登入**(客戶姓名 / 電話 / 地址是個資,只留在
自己電腦,不進版控);也可以部署到雲端主機給家易開固定網址用(`GY_PASSWORD` 整站共用密碼、
`GY_DATA_DIR` 把資料放程式碼目錄外),見下方「部署方式」。

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

這些是裝在 Windows 帳號個人目錄的 Claude Code 工具設定,**不隨 git clone 帶過去**,換電腦
要另外裝。這裡只記錄「有裝什麼、怎麼裝」,方便兩台電腦對齊,不是每次都要真的裝——只有真的
要用某個功能時才裝那一個。**兩台電腦的 SAC(智慧型應用程式控制)開關不同,會不會裝得起來
看那台的 SAC 狀態,不是程式碼問題**(見下表備註)。

| Skill | 用途 | 怎麼裝 | 備註 |
|---|---|---|---|
| **agent-browser** | 瀏覽器自動化(dogfood 探索式測試、走位截圖、示意圖 Artifact 發布前預覽) | `npm i -g agent-browser && agent-browser install`(實際下載 Chrome ~196MB,不是估的 50MB) | 套件內含**未簽章 exe**,SAC 開啟時會被硬擋(「應用程式控制原則已封鎖此檔案」,無法繞過)。2026-10-01 兩台一度一台能用一台不能;當天使用者把原本開著 SAC 的那台關掉後,裝起來也驗證能正常開瀏覽器/讀畫面/點連結 |
| **playwright**(Claude Code plugin,非上面那個 npm 工具) | 瀏覽器自動化的另一個來源,微軟官方 `@playwright/mcp`,走官方 marketplace | `claude plugin install playwright@claude-plugins-official --scope project` | 2026-10-01 裝在 SAC 開啟的那台當 agent-browser 的替代方案;還沒實際觸發過(第一次用會下載瀏覽器執行檔),能不能繞過 SAC 未驗證 |
| **frontend-design**(Claude Code plugin) | 前端 UI/排版工作時的被動技能,避免「一看就是 AI 做的」通用設計 | `claude plugin install frontend-design@claude-plugins-official --scope project` | 純 `SKILL.md` 文字指示,無執行檔,兩台都能裝 |
| find-skills | 幫忙找/裝其他 skill 的 meta skill | 通常內建或用它裝別的 skill 時順便有 | |
| pdf | PDF 讀取 / 合併 / 填表等操作 | **不用裝** —— Claude Code 環境本身已內建(`anthropic-skills:pdf`) | |

## 部署方式(給郡碩用)

**雲端架站(2026-10 起的主要交付方式)**:部署到 Render(Starter 方案,$7.25 美金/月),
家易開固定網址就能用,不用裝任何東西。步驟見 [app/docs/上雲-render.md](app/docs/上雲-render.md)。
(原本評估過 PythonAnywhere 免費版,但它只吃 WSGI、FastAPI 是 ASGI,兩次部署都卡死
被砍掉,已經放棄這條路,詳情見該文件開頭說明。)

> 原本規劃過打包成免安裝 .exe 給家易雙擊執行(`PyInstaller`),但這台開發機的「智慧型應用
> 程式控制(SAC)」會硬擋未簽章的 exe,從沒成功交付過;2026-10-02 決定改走雲端架站為主,
> 相關程式(`launch.py`、`打包.bat`)與文件(`交付說明.md`、`臨時分享.md`)已經拿掉,
> 沿革見 `app/docs/開發路線.md`。

---

## 文件(`app/docs/`)

| 檔案 | 內容 |
|---|---|
| **開發路線.md** | ★ 現在做到哪、決定了什麼、下一步 —— **要繼續施工先看這份** |
| **工作日誌.md** | ★ 每次施工細項 + 待辦 / 決策清單 —— **換電腦接手先看這份** |
| **定價分析.md** | 單位成本往上堆疊 → 建議售價;判斷哪條產品線該擴 / 該收(非帳本,估算用) |
| **上雲-render.md** | ★ 部署到 Render(Starter 方案):固定網址、共用密碼,家易隨時連 —— **目前的主要交付方式** |
| **展示腳本.md** | demo 給郡碩前自己先跑一遍的逐步走位 + 對數字 + 提問清單 |
| **報稅參考.md** | 郡碩的 403 / 營所稅申報現況、留抵稅額、待釐清事項 |
| 系統文件.md | 資料庫結構、路由、關鍵邏輯 |
| **使用手冊.md** | ★ 給操作者(家易)的日常操作說明,圖文並茂 |
| **功能手冊.md** | ★ 給同事看的完整功能 / 記帳邏輯對照,圖文並茂 |
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
