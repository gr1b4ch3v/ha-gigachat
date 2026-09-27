# Участие в разработке

Перед крупными изменениями откройте issue, чтобы обсудить подход.

## Окружение

Нужен Python 3.14, как в Home Assistant 2026.9.

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
```

## Проверки

```bash
ruff check . && ruff format --check .
pytest --cov=custom_components.gigachat
```

Тесты не обращаются к сети, API ГигаЧата в них подменяется. Версия тестового окружения закреплена
в `requirements_test.txt` и соответствует версии Home Assistant.

## Тексты интерфейса

Исходные тексты лежат в `custom_components/gigachat/strings.json` на английском. Меняя их, обновите также
`translations/en.json` и `translations/ru.json`. Чтобы добавить язык, скопируйте `translations/en.json`
в `translations/<код языка>.json` и переведите значения. Ключи и плейсхолдеры вроде `{model}` не меняйте.
Ссылки в тексты не вставляйте: hassfest это запрещает, для них есть плейсхолдеры.

## Pull request

- Одна логическая правка на PR.
- Новое поведение покрыто тестами.
- Обновлены `README.md`, `README.en.md` и `CHANGELOG.md`, если изменение заметно пользователю.
- В коде, тестах и логах нет ключей и токенов.

---

# Contributing

Before a large change, open an issue to discuss the approach.

## Environment

Python 3.14 is required, the same version as in Home Assistant 2026.9.

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
```

## Checks

```bash
ruff check . && ruff format --check .
pytest --cov=custom_components.gigachat
```

The tests do not access the network, the GigaChat API is mocked. The test environment is pinned in
`requirements_test.txt` to match the Home Assistant version.

## UI strings

The source of the strings is `custom_components/gigachat/strings.json` (English). When you change strings,
also update `translations/en.json` and `translations/ru.json`. To add a language, copy `translations/en.json`
to `translations/<language code>.json` and translate the values. Keep the keys and placeholders such as
`{model}` unchanged. Do not put links into the strings: hassfest rejects them, use placeholders instead.

## Pull requests

- One logical change per pull request.
- New behavior is covered by tests.
- `README.md`, `README.en.md` and `CHANGELOG.md` are updated when users notice the change.
- No keys or tokens in code, tests or logs.
