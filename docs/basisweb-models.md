# Basisweb models

The `basisweb` provider uses `https://api.basisweb.ru/v1/chat/completions`.
Its catalog was checked against the provider's authenticated `/v1/models`
response on 2026-11-01: all 52 text models are included. Image, audio and
video models use separate APIs and are excluded from the translation list.
GPT 5.5 is retained for compatibility with saved sessions.

Model identifiers, context windows and supported reasoning levels come from
the provider. These are gateway capabilities, not upstream model guarantees.
Requests remain limited to 8,192 output tokens, 10 requests per minute and
one concurrent request per key. Existing GPT models keep their low default;
new models use the provider's advertised default reasoning level.

Reasoning options are sent only when thinking is enabled and the model
advertises support. An unsupported selected level is rejected before a
request is sent. Models without reasoning support ignore the global level.
Credentials belong in application settings and are never part of this catalog.
