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

## Поведение при rate limit
При ответе Twitch API `429 Too Many Requests` запрос повторяется до пяти раз с экспоненциальной задержкой; при превышении лимита генерируется ошибка.
