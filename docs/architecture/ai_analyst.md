# AI Analyst — trading_bot integration

**Статус:** каркас (2026-07)  
**Пакет:** `Code_base/ai_analyst`  
**Плагин:** `plugins/ai_analyst` (extension, **не** стратегия)

---

## Роль

LLM-аналитик поверх runtime / universe / journal / history.  
Analyst mode — только read. Operator mode — gated control: `place_order` / `close_position` /
`close_all` / `cancel_order` (live/quik → `confirm_live=true`), плюс `system_halt` / `system_resume`.

```text
REPL ask → TradingAiAnalystPlugin → AIAnalystService → tools → services
```

---

## Конфиг

```yaml
plugins:
  ai_analyst:
    enabled: true
    default_provider: mock
    profiles:
      mock:
        provider_type: mock
        enabled: true
```

Смена провайдера: `ollama` / `openai` / … через profiles (см. Code_base/ai_analyst README).

---

## REPL

| Команда | Действие |
|---------|----------|
| `ask <text>` | вопрос аналитику (tools on) |
| `ask --no-tools <text>` | one-shot без tool loop |
| `show ai` | provider, tools, last ask |
| `show plugins` | AI в extensions (не в `show strategy`) |

---

## Tools (v1)

`get_runtime_status`, `get_universe`, `query_journal`, `history_summary`, `list_strategies`, `system_halt`, `system_resume`.

Событие: `ai.analyst.response` → storage journal.

---

## Вне scope

Meta/regime на каждый candle, NLP новостей, ордера через AI, chat UI.
