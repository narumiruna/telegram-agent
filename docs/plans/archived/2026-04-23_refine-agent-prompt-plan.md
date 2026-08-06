# Refine Agent Prompt

## Goal

重構 Telegram agent 的核心指令與人格上下文，使優先級、工具誠實性、上下文承接、回覆契約及人格邊界更明確，同時減少重複與無效 token。

## Plan

- [x] 在 `tests/test_context_files.py` 增加核心指令契約測試，涵蓋優先級、外部內容防注入、工具失敗誠實性及人格服從正確性的要求；focused test 起初如預期失敗於缺少優先級契約。
- [x] 重構 `src/telegramagent/llm.py` 的 `_chat_instructions()`，以清楚章節表達角色、優先級、上下文、工具、輸出及安全契約；`tests/test_context_files.py` 與 `tests/test_llm.py` 共 18 tests 通過。
- [x] 精簡並重寫 `SOUL.md`，保留「すーちゃん風」的虛構人格、繁體中文語氣及真人邊界，移除重複和不影響行為的背景資料；smoke check 證實未截斷、組合順序正確，prompt 由約 8,475 降至 6,877 字元。
- [x] 執行 `uv run ruff format --check`、`uv run ruff check .`、`uv run ty check .`、`uv run pytest -q tests`；全部通過，完整測試為 170 passed。

## Completion Checklist

- [x] 核心規則的優先順序明確且不依賴隱含推論。
- [x] 工具不可用、失敗或無結果時不得宣稱完成，外部內容不得覆寫指令。
- [x] 編號選項、前文 URL、`display_items`／`response_contract` 既有行為均保留。
- [x] 人格自然但不冒充真人，且不得凌駕準確性、安全或工具契約。
- [x] 所有 repository quality gates 通過。
