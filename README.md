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

## Обновление sha256Hash для GQL операций
Twitch периодически изменяет хэши Persisted Query для `ViewerDropsDashboard`,
`Inventory`, `IncrementDropCurrentSessionProgress` и `ClaimDropReward`. При
неверных значениях API возвращает ошибку `PersistedQueryNotFound`.

1. Откройте [twitch.tv](https://www.twitch.tv) и включите инструменты разработчика
   (Network → Preserve log).
2. Выполните действие, связанное с нужной операцией, чтобы в журнале появился
   запрос к `https://gql.twitch.tv/gql`.
3. В теле запроса найдите поле `extensions.persistedQuery.sha256Hash` и
   скопируйте значение.
4. Замените соответствующее значение в `ops/ops.json` и сохраните файл.
5. Закоммитьте изменения и при необходимости отправьте pull‑request.
