Twitch Drops — API Miner (No Video) + GUI

Поддержка TXT формата login:password (а также login:password:totp:proxy).
Прокси из последнего поля используется при запросах к Twitch API.

Запуск
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

Twitch периодически меняет хэши Persisted Query для операций ViewerDropsDashboard, Inventory, IncrementDropCurrentSessionProgress, ClaimDropReward (и при использовании списка каналов — DropsCampaignDetails).
При неверных значениях API возвращает ошибку PersistedQueryNotFound.

Откройте twitch.tv и включите DevTools (вкладка Network, включите Preserve log).

Выполните действие, связанное с нужной операцией, чтобы в лог попал запрос к https://gql.twitch.tv/gql.

В теле запроса найдите поле extensions.persistedQuery.sha256Hash и скопируйте значение.

Обновите соответствующее значение в ops/ops.json и сохраните файл.

Перезапустите приложение.

Поведение при rate limit

При ответе Twitch API 429 Too Many Requests запрос автоматически повторяется с экспоненциальной задержкой до 5 раз.
Если лимит попыток исчерпан, выбрасывается ошибка с понятным сообщением.