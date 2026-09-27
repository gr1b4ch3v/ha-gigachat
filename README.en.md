<p align="center">
  <img src="https://raw.githubusercontent.com/gr1b4ch3v/ha-gigachat/main/custom_components/gigachat/brand/icon.png" alt="GigaChat" width="120">
</p>

<h1 align="center">GigaChat for Home Assistant</h1>

<p align="center">
  <a href="https://github.com/gr1b4ch3v/ha-gigachat/releases"><img src="https://img.shields.io/github/v/release/gr1b4ch3v/ha-gigachat" alt="Release"></a>
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.9%2B-18BCF2.svg" alt="Home Assistant 2026.9+">
  <a href="https://github.com/gr1b4ch3v/ha-gigachat/actions/workflows/validate.yml"><img src="https://github.com/gr1b4ch3v/ha-gigachat/actions/workflows/validate.yml/badge.svg" alt="Validate"></a>
  <a href="https://github.com/gr1b4ch3v/ha-gigachat/actions/workflows/tests.yml"><img src="https://github.com/gr1b4ch3v/ha-gigachat/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/gr1b4ch3v/ha-gigachat/blob/main/README.md"><img src="https://img.shields.io/badge/docs-%D0%BD%D0%B0%20%D1%80%D1%83%D1%81%D1%81%D0%BA%D0%BE%D0%BC-lightgrey.svg" alt="На русском"></a>
</p>

This integration connects [GigaChat](https://developers.sber.ru/docs/ru/gigachat/overview) (ГигаЧат), the LLM by Sber,
to Home Assistant: a voice assistant for Assist and AI tasks for automations. All you need is a key from
GigaChat Studio.

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=gr1b4ch3v&repository=ha-gigachat&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open in HACS"></a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=gigachat"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Add integration"></a>
</p>

## Features

- Assist conversation agent: answers questions, reads device states and controls devices.
- AI tasks: text or JSON following a schema, also from a camera snapshot, and 1024x1024 images.
- Token balance sensors per model and a counter of the tokens used.
- Several agents with different models and instructions.
- Russian and English UI.

## Installation

Requires Home Assistant 2026.9 or newer.

**HACS.** Click "Open in HACS" above or add the repository manually: HACS → ⋮ → *Custom repositories* →
`https://github.com/gr1b4ch3v/ha-gigachat`, type *Integration*. Install GigaChat and restart Home Assistant.

**Manually.** Extract `gigachat.zip` from the [releases](https://github.com/gr1b4ch3v/ha-gigachat/releases)
page into `config/custom_components/` and restart Home Assistant.

## Configuration

1. Get a key in [GigaChat Studio](https://developers.sber.ru/studio/workspaces): GigaChat API project →
   *API settings* → *Get key*.
2. Go to *Settings → Devices & services → Add integration → GigaChat*.
3. Paste the key and choose the API version: `GIGACHAT_API_PERS` for individuals, `GIGACHAT_API_B2B` or
   `GIGACHAT_API_CORP` for businesses with prepaid packages or pay-as-you-go billing.

You then get the "GigaChat" conversation agent and the "GigaChat AI Task" entity. Change the model and
instructions with ⋮ → *Reconfigure*.

### Voice assistant

In *Settings → Voice assistants → Add assistant* choose GigaChat as the conversation agent. Enable
"Prefer handling commands locally" so that Home Assistant runs simple commands such as "turn on the light"
itself, without spending tokens.

The assistant only sees the entities listed under *Expose* and relies on their names, so keep them unambiguous.

## Models

The integration gets the list of models from your account and picks the best available one by default.
See [Sber's documentation](https://developers.sber.ru/docs/ru/gigachat/models/main) for how the models
differ. For images the integration automatically picks a model that can draw them.

## Pricing

The free Freemium package for individuals and personal use is valid for 12 months: 250M tokens for Lite,
40M for Pro, 25M for Max, 50M for Ultra. See the
[GigaChat API tariffs](https://developers.sber.ru/docs/ru/gigachat/api/tariffs) for details.

## Sensors

- `sensor.gigachat_*_token_balance`: remaining tokens in each package. Pay-as-you-go accounts have no
  balance.
- `sensor.gigachat_tokens_used`: tokens used by the integration. Kept across restarts.

## Examples

Ready-made automations live in the
[examples](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/README.md) folder:

| Example | What it does |
|---|---|
| [An interesting fact every hour](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/hourly_fact.yaml) | From 9 to 21 a speaker tells a fact-checked fact |
| [Coffee fact](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/coffee_fact.yaml) | A fact after the coffee machine turns off |
| [Morning briefing](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/morning_briefing.yaml) | Weekday weather and calendar events |
| [Evening check](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/evening_report.yaml) | At 23:00 tells which lights are on and what is open |
| [Bedtime story](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/bedtime_story.yaml) | A new fairy tale in the kids' room every evening |
| [Laundry done](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/laundry_done.yaml) | A reminder to hang the laundry |
| [Who is at the door](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/door_camera.yaml) | Describes the camera snapshot when the doorbell rings |
| [Weather for a walk](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/weather_walk.yaml) | JSON answer: is it good for a walk |
| [Morning postcard](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/image_postcard.yaml) | A picture for the new day |
| [Tokens running low](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/examples/low_balance.yaml) | A notification based on the balance sensor |

A call from a script or an automation:

```yaml
action: ai_task.generate_data
data:
  task_name: Greeting
  entity_id: ai_task.gigachat_ai_task
  instructions: Write a short good morning greeting.
response_variable: greeting
# Answer: {{ greeting.data }}
```

## How it works

- The root certificate of the Russian Ministry of Digital Development ships with the integration, TLS
  verification stays on.
- Individuals may run one request at a time, so requests are queued and 429 and 5xx responses are retried.
- Every agent has a stable `X-Session-ID`, so GigaChat caches the unchanged part of the prompt. Cached tokens
  are not billed.
- Home Assistant tool schemas are converted to the subset of JSON Schema that GigaChat understands.

## Privacy

The key is stored in Home Assistant and is only sent to the Sber authorization server. GigaChat receives the
text of the requests and, when home control is enabled, the names and states of the exposed entities. Images and
attachments are deleted from the GigaChat storage right after use.

## License

[MIT](https://github.com/gr1b4ch3v/ha-gigachat/blob/main/LICENSE). Not an official Sber product. Use of the
GigaChat API is governed by Sber's [user agreement](https://developers.sber.ru/docs/ru/policies/gigachat/agreement).
