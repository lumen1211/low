# Twitch Drops — API Miner (No Video) + GUI
**Поддержка TXT формата `login:password`** (а также `login:password:totp:proxy`).

## Запуск
```bash
pip install -r requirements.txt
python -m playwright install chromium   # только для онбординга
# можно дать как CSV, так и TXT:
python main.py --accounts accounts.txt --onboarding   # автологин → cookies/<login>.json
python main.py --accounts accounts.txt                # GUI майнер (без видео)
```
TXT правила:
- строка: `login:password` (минимум);
- опционально: `login:password:totp:proxy` (3-й и 4-й токены не обязательны);
- пустые строки и строки с `#` игнорируются.

### Обновление sha256 хэшей

Persisted Query хэши для GraphQL‑операций находятся в `src/ops.py`.
Если Twitch меняет их, получите актуальные значения через DevTools:

1. Откройте [twitch.tv](https://www.twitch.tv/) в браузере и авторизуйтесь.
2. Откройте инструменты разработчика → **Network** и включите запись.
3. Совершите действие, вызывающее нужную операцию (например, откройте инвентарь или клейм).
4. Найдите запрос `gql`, перейдите на вкладку **Payload** и скопируйте значение
   `extensions.persistedQuery.sha256Hash`.
5. Обновите соответствующее поле `sha256` в словаре `OP` и сохраните файл.

Если хэш неизвестен, оставьте `sha256` равным `None` — функция `get_op` сообщит об этом при попытке вызова.
