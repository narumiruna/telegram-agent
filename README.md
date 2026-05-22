# telegramagent

一個簡單的 Telegram AI 機器人，使用 Telegram Bot API long polling，並透過 OpenAI-compatible Chat Completions API 回覆訊息。

## 設定

建立 `.env`：

```env
BOT_TOKEN=你的 Telegram Bot Token
# 選填：逗號分隔 chat_id 或 user_id；空白代表不限制
BOT_WHITELIST=
# 兩個 bot 互相 reply 時，最多允許連續回覆幾次；0 代表完全不回覆其他 bot
BOT_MAX_CONSECUTIVE_REPLIES_TO_BOTS=1
# Agent Skills 目錄；BOT_ENABLED_SKILLS 空白代表載入目錄內全部 skills
BOT_SKILLS_DIR=.agents/skills
BOT_ENABLED_SKILLS=
# 選填：可透過 Telegram 管理 skills 的 chat_id 或 user_id；空白時沿用 BOT_WHITELIST
BOT_SKILL_ADMINS=

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=你的 API Key
OPENAI_MODEL=gpt-5.4-mini
```

## 執行

```bash
uv run telegramagent
```

或用 Docker Compose 讀取 `.env` 並啟動 bot：

```bash
docker compose up -d --build
```

查看 logs：

```bash
docker compose logs -f telegramagent
```

停止：

```bash
docker compose down
```

## 指令

- `/start`：顯示簡介
- `/help`：顯示說明
- `/id`：顯示 chat/user ID，方便設定白名單
- `/reset`：清除這個聊天室的對話記憶
- `/ask <問題>`：詢問 AI 助理
- `/skills add <package>`：在本機專案使用 `npx skills add <package> --yes --copy` 安裝 Agent Skills
- `/skills list`：列出已安裝 Agent Skills

也可以直接傳一般文字給機器人。

## 群組回應規則

在私人聊天室中，機器人會回覆一般文字。 在群組或超級群組中，為了避免打擾聊天，只有以下兩種情況會回應：

1. 訊息中 `@` 機器人帳號，例如 `@your_bot 你好`
2. 直接 reply 機器人的訊息

## Agent Skills

Bot 使用 Pydantic AI Agent 回覆訊息，並會在啟動時載入 Agent Skills 作為 instructions。

預設 skills 目錄：

```text
.agents/skills/<skill-name>/SKILL.md
```

範例：

```md
---
name: chat-style
description: Telegram 回覆風格。Use when replying to Telegram messages.
---

# Chat Style

- 用繁體中文。
- 回答要短。
```

設定 `BOT_ENABLED_SKILLS=chat-style,other-skill` 可只載入指定 skills；留空會載入 `BOT_SKILLS_DIR` 內全部 skills。

目前 skills 會作為 Pydantic AI instructions 使用，不會執行 skill 內附帶的本機腳本。

也可以從 Telegram 對話安裝 skills：

```text
/skills add vercel-labs/agent-skills --skill commit
/skills list
```

也支援自然語言安裝請求，例如：

```text
安裝 narumiruna/skills 的 skills 所有
```

會轉成：

```bash
npx skills add narumiruna/skills --skill '*' --agent universal --yes --copy
```

`/skills add` 會在 bot 執行所在的專案目錄呼叫 `npx skills add ... --yes --copy`，安裝完成後自動重新載入 skills。預設會加上 `--agent universal`，只寫入 bot 會讀取的 `.agents/skills`，避免安裝到所有 agent 目錄。再次安裝前會先偵測已存在的 skills；若要強制重裝可加 `--force`。

Docker 映像已包含 `git` / `nodejs` / `npm` / `npx`，Compose 會把本機 `./.agents` 掛載到容器中讓 skills 可持久化，並以 root 在容器內執行以避免掛載目錄權限造成安裝失敗。

## Bot 對 Bot 結束話題機制

當訊息來源也是 Telegram bot 時，程式會先交給「結束話題判斷 agent」判斷是否應該靜默結束：

- 如果對話只是 `好` / `好的` / `了解` 之類的收尾或重複附和，機器人不再回覆，避免兩個 bot 無限互答。
- 如果對方 bot 提出明確新問題或有新資訊，判斷 agent 可允許繼續回覆。
- `BOT_MAX_CONSECUTIVE_REPLIES_TO_BOTS` 是保險上限；即使判斷 agent 沒有結束，超過上限也會停止。
