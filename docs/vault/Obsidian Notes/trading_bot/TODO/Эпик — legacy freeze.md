# Эпик — legacy freeze

**Приоритет:** P0  
**Статус:** утверждено  

---

## Правило

**Legacy-код в корне репо работает — не расширять.**

Разрешено:
- bugfix критичных ошибок
- copy-paste **из** legacy **в** target при миграции
- security: убрать secrets из git (отдельная задача)

Запрещено:
- новые стратегии в `Strategies/`
- новые фичи в `structy.py`, `UI.py`
- рефакторинг ради красоты без parity в target

---

## Финал эпика

- [ ] Переместить legacy в `legacy/` после parity checklist ([[Архитектура — целевая (microkernel)#Parity checklist]])
- [ ] README в `legacy/` с датой freeze
- [ ] Root README: «запуск v2 → host/main.py»
