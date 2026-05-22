# telegramagent

一個簡單的 Telegram AI 機器人，使用 Telegram Bot API long polling，並透過 OpenAI-compatible Chat Completions API 回覆訊息。

## 設定

建立 `.env`：

```env
BOT_TOKEN=你的 Telegram Bot Token
# 選填：逗號分隔 chat_id 或 user_id；空白代表不限制
BOT_WHITELIST=

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=你的 API Key
OPENAI_MODEL=gpt-5.4-mini
```

## 執行

```bash
uv run telegramagent
```

## 指令

- `/start`：顯示簡介
- `/help`：顯示說明
- `/id`：顯示 chat/user ID，方便設定白名單
- `/reset`：清除這個聊天室的對話記憶
- `/ask <問題>`：詢問 AI 助理

也可以直接傳一般文字給機器人。

## 群組回應規則

在私人聊天室中，機器人會回覆一般文字。 在群組或超級群組中，為了避免打擾聊天，只有以下兩種情況會回應：

1. 訊息中 `@` 機器人帳號，例如 `@your_bot 你好`
2. 直接 reply 機器人的訊息
