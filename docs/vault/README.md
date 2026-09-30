# Vault documentation

Obsidian / Word notes for trading_bot. Path in the repo: `docs/vault/`.

```text
docs/vault/
├── README.md                 ← this file
├── Obsidian Notes/           ← vault (wikilinks)
│   └── trading_bot/          ← product notes + TODO epics
└── Word/                     ← .docx (as needed)

docs/architecture/            ← canonical markdown specs (code repo)
docs/todo.md                  ← backlog summary
```

## Start here

| Task | Note |
|------|------|
| Open backlog | `docs/todo.md` |
| Epics | `Obsidian Notes/trading_bot/TODO/README.md` |
| Vault map | [[Карта проекта]] |
| Product memo | [[Продуктовый меморандум trading_bot]] |
| Philosophy | [[Философия архитектуры]] |
| Universe | [[Universe selection]] |
| Target architecture | [[Архитектура — целевая (microkernel)]] |
| Data flow | [[DATA FLOW]] |
| Event contract | [[Event Contract]] |

Historical notes may still mention `Документация/`, `UI.py`, `Drivers/` —
those paths were removed from `main` (see git tag `legacy-v1`).
Use `docs/vault/` and the live packages `engine/`, `host/`, `services/`, `ui/`.
