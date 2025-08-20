# Twitch Drops — API Miner (No Video) + GUI

**Поддержка TXT формата `login:password`** (а также `login:password:totp:proxy`).
Прокси из последнего поля используется при запросах к Twitch API.

## Запуск

```bash
pip install -r requirements.txt
python -m playwright install chromium   # только для онбординга
# можно дать как CSV, так и TXT:
python main.py --accounts accounts.txt --onboarding   # автологин → cookies/<login>.json
python main.py --accounts accounts.txt                # GUI майнер (без видео)
TXT правила
строка: login:password (минимум);

опционально: login:password:totp:proxy (3-й и 4-й токены не обязательны);

пустые строки и строки с # игнорируются.

Обновление sha256Hash для GQL операций
Twitch периодически меняет хэши Persisted Query для операций
ViewerDropsDashboard, Inventory, DropCurrentSessionContext,
DropsPage_ClaimDropRewards (и при использовании списка каналов — DropCampaignDetails,
ранее DropsCampaignDetails).
При неверных значениях API возвращает ошибку PersistedQueryNotFound.

Откройте https://www.twitch.tv и включите DevTools (вкладка Network, включите Preserve log).

Выполните действие, чтобы в лог попал запрос к https://gql.twitch.tv/gql.

В теле запроса найдите extensions.persistedQuery.sha256Hash и скопируйте значение.

Обновите соответствующее значение в ops/ops.json и сохраните файл.

Перезапустите приложение.

Обновление Client-Version и Client-Integrity
Twitch может менять значения заголовков `Client-Version` и `Client-Integrity`,
используемых в GQL запросах. После получения cookies запустите:

```bash
python scripts/update_ci.py --accounts accounts.txt
```

Скрипт откроет страницу Drops в headless браузере, перехватит первый запрос
`https://gql.twitch.tv/gql` и сохранит найденные значения в `ci/<login>.json`.
Эти файлы автоматически подхватываются при запуске приложения.

Поведение при rate limit
При ответе Twitch API 429 Too Many Requests запрос автоматически повторяется
с экспоненциальной задержкой до 5 раз. Если лимит попыток исчерпан, выбрасывается
ошибка с понятным сообщением.