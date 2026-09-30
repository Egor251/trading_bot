# Эпик — UI

**Приоритет:** P2  
**Статус:** каркас (Tauri + static Next + BFF)  
**Канон:** `docs/architecture/operator_ui.md`

---

## Решение

- **Стек:** Tauri 2 desktop (канон) + static Next export (`ui/web`) + FastAPI BFF; Engine bridge TCP `:8765`
- **Web:** отдельная browser-сборка `python run_operator.py --web` (remote/server)
- Legacy Dear PyGui удалён; ориентир по depth — git history `ui/dpg/`

## Экраны

| Панель | Статус |
|--------|--------|
| Runtime + Kill | **есть** (web/desktop) |
| Consilium / tape | **есть** |
| Charts OHLC / equity | **есть** (lightweight-charts) |
| Phantom forms | **есть** (depth backlog) |
| Desk terminal density | backlog |
| Strategy dossier / viz | backlog |
| i18n in React | backlog |

## Задачи

- [x] Tauri shell + tray + hide-on-close
- [x] Static Next export + platform flag desktop/web
- [x] BFF REST + WS
- [ ] Desk terminal density (DPG parity)
- [ ] Phantom scrub / schema params
- [ ] Strategy dossier / viz
- [ ] i18n + notify settings in React
