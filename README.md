<p align="center">
  <img src="https://raw.githubusercontent.com/gr1b4ch3v/ha-gigachat/main/custom_components/gigachat/brand/icon.png" alt="GigaChat" width="120">
</p>

<h1 align="center">GigaChat для Home Assistant</h1>

<p align="center">
  <a href="https://github.com/gr1b4ch3v/ha-gigachat/releases"><img src="https://img.shields.io/github/v/release/gr1b4ch3v/ha-gigachat" alt="Release"></a>
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.9%2B-18BCF2.svg" alt="Home Assistant 2026.9+">
  <a href="https://github.com/gr1b4ch3v/ha-gigachat/actions/workflows/validate.yml"><img src="https://github.com/gr1b4ch3v/ha-gigachat/actions/workflows/validate.yml/badge.svg" alt="Validate"></a>
  <a href="https://github.com/gr1b4ch3v/ha-gigachat/actions/workflows/tests.yml"><img src="https://github.com/gr1b4ch3v/ha-gigachat/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/gr1b4ch3v/ha-gigachat/blob/main/README.en.md"><img src="https://img.shields.io/badge/docs-English-lightgrey.svg" alt="English"></a>
</p>

Интеграция подключает [GigaChat](https://developers.sber.ru/docs/ru/gigachat/overview) к Home Assistant:
голосовой помощник для Assist и задачи ИИ для автоматизаций. Нужен только ключ из GigaChat Studio.

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=gr1b4ch3v&repository=ha-gigachat&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Открыть в HACS"></a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=gigachat"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Добавить интеграцию"></a>
</p>

## Возможности

- Диалоговая система для Assist: отвечает на вопросы, видит состояние устройств и управляет ими.
- Задачи ИИ: текст или JSON по схеме, в том числе по снимку с камеры, и картинки 1024x1024.
- Сенсоры остатка токенов по моделям и счётчик израсходованных токенов.
- Несколько агентов с разными моделями и инструкциями.
- Интерфейс на русском и английском.

## Установка

Нужен Home Assistant 2026.9 или новее.

**HACS.** Нажмите «Открыть в HACS» выше или добавьте репозиторий вручную: HACS → ⋮ →
*Пользовательские репозитории* → `https://github.com/gr1b4ch3v/ha-gigachat`, тип *Integration*.
Установите GigaChat и перезапустите Home Assistant.

**Вручную.** Распакуйте `gigachat.zip` со страницы [релизов](https://github.com/gr1b4ch3v/ha-gigachat/releases)
в `config/custom_components/` и перезапустите Home Assistant.

## Настройка

1. Получите ключ в [GigaChat Studio](https://developers.sber.ru/studio/workspaces): проект GigaChat API →
   *Настройки API* → *Получить ключ*.
2. Откройте *Настройки → Устройства и службы → Добавить интеграцию → GigaChat*.
3. Вставьте ключ и выберите версию API: `GIGACHAT_API_PERS` для физлиц, `GIGACHAT_API_B2B` или
   `GIGACHAT_API_CORP` для юрлиц с предоплатой или оплатой по факту.

После настройки появятся диалоговая система «GigaChat» и задача ИИ «GigaChat AI Task». Модель и инструкции
меняются через ⋮ → *Перенастроить*.

### Голосовой помощник

В *Настройки → Голосовые ассистенты → Добавить ассистента* выберите агент диалога GigaChat. Включите
«Предпочитать локальную обработку команд», тогда простые команды вроде «включи свет» Home Assistant
выполнит сам, без расхода токенов.

Помощник видит только объекты из списка *Открытые объекты* и ориентируется на их имена, поэтому имена
должны быть однозначными.

## Модели

Список моделей интеграция получает из вашего аккаунта и по умолчанию выбирает лучшую из доступных. Чем
модели отличаются, написано в [документации Сбера](https://developers.sber.ru/docs/ru/gigachat/models/main).
Для картинок интеграция сама выбирает модель, которая умеет их рисовать.

## Стоимость

Бесплатный пакет Freemium выдаётся физлицам для личного использования на 12 месяцев: Lite 250 млн токенов,
Pro 40 млн, Max 25 млн, Ultra 50 млн. Подробнее в
[тарифах GigaChat API](https://developers.sber.ru/docs/ru/gigachat/api/tariffs).

## Сенсоры

- `sensor.gigachat_*_token_balance`: остаток токенов в каждом пакете. У аккаунтов с оплатой по факту
  баланса нет.
- `sensor.gigachat_tokens_used`: токены, израсходованные интеграцией. Сохраняется после перезапуска.

## Примеры

Готовые автоматизации лежат в папке
[examples](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/README.md):

| Пример | Что делает |
|---|---|
| [Интересный факт каждый час](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/hourly_fact.yaml) | С 9 до 21 колонка рассказывает проверенный факт |
| [Кофе-справка](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/coffee_fact.yaml) | Факт после выключения кофемашины |
| [Утренняя сводка](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/morning_briefing.yaml) | По будням погода на день и события календаря |
| [Вечерний обход](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/evening_report.yaml) | В 23:00 сообщает, где горит свет и что открыто |
| [Сказка на ночь](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/bedtime_story.yaml) | Каждый вечер новая сказка в детской |
| [Стирка закончилась](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/laundry_done.yaml) | Напоминание развесить бельё |
| [Кто у двери](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/door_camera.yaml) | Описание снимка с камеры при звонке в дверь |
| [Погода для прогулки](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/weather_walk.yaml) | Ответ в JSON: можно ли гулять |
| [Утренняя открытка](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/image_postcard.yaml) | Картинка на новый день |
| [Заканчиваются токены](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/low_balance.yaml) | Уведомление по сенсору баланса |

Вызов из скрипта или автоматизации:

```yaml
action: ai_task.generate_data
data:
  task_name: Приветствие
  entity_id: ai_task.gigachat_ai_task
  instructions: Придумай короткое утреннее приветствие.
response_variable: greeting
# Ответ: {{ greeting.data }}
```

## Как это работает

- Корневой сертификат Минцифры встроен в интеграцию, проверка TLS не отключается.
- Физлицу разрешён один одновременный запрос, поэтому запросы идут по очереди, а ответы 429 и 5xx
  повторяются.
- У каждого агента постоянный `X-Session-ID`, и GigaChat кэширует неизменную часть промпта. Кэшированные
  токены не оплачиваются.
- Схемы инструментов Home Assistant приводятся к подмножеству JSON Schema, которое понимает GigaChat.

## Конфиденциальность

Ключ хранится в Home Assistant и отправляется только на сервер авторизации Сбера. В GigaChat уходят
тексты запросов, а при включённом управлении домом ещё имена и состояния открытых объектов. Картинки
и вложения удаляются из хранилища GigaChat сразу после использования.

## Лицензия

[MIT](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/LICENSE). Проект не является официальным продуктом
Сбера. Использование GigaChat API регулируется
[пользовательским соглашением](https://developers.sber.ru/docs/ru/policies/gigachat/agreement) Сбера.
