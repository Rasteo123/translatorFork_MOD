# GitHub Release Draft

Suggested tag: `v10.5.16`  
Suggested title: `translatorFork_MOD v10.5.16`  
Base release: `translatorFork_MOD-37346b9` (`2026-04-19`)  
Target snapshot: current `main` (`759b9b4`) + local Reader/TTS update (`2026-04-21`)

## Release Body

## Что нового

- `Gemini Reader` получил новые TTS-модели: `Gemini 3.1 Flash TTS Preview`, `Gemini 2.5 Flash TTS Preview` и `Gemini 2.5 Pro TTS Preview`.
- В `Live API` добавлен новый режим `Автор + Муж./Жен. роли` с отдельными голосами для авторского текста, мужских и женских реплик.
- AI-подготовка сценария для role-based режима стала строже: `Author/Male/Female` размечаются отдельно, авторские хвосты выносятся из прямой речи, запрещены лишние вставки и выдуманные слова.
- В `Gemini Reader` добавлен экспорт видео: выбор картинки обложки, сборка `MP4` из готовых `MP3` и проверки наличия `ffmpeg`/`ffprobe`.
- Улучшена пакетная и параллельная озвучка: новые лимиты для `Flash TTS`, интервалы между главами, более аккуратная склейка live-сегментов и обработка коротких реплик/пауз.
- Сборка релиза теперь выпускается в двух вариантах: компактный `translator-only` и полный `full`.

## Улучшения

- В интерфейсе `Gemini Reader` добавлены настройки трёх голосов, переключение голосовых режимов и отдельный блок с редактируемыми промптами AI-подготовки и TTS.
- Обновлён `README` и добавлен полноценный пользовательский гайд в `docs/user-guide`.
- Доработан layout сессии перевода и уплотнены рабочие виджеты основного интерфейса.
- Улучшена обработка больших книг и сценарий dual-build для релизной сборки Windows.
- Добавлен отдельный `build_translator_linux.sh`, а `translatorFork-translator-only.spec` приведён к более корректной сборке translator-only бинаря на Linux и Windows.

## Важно

- Это следующий релиз после `translatorFork_MOD-37346b9`, опубликованного `19 April 2026`.
- Режим `Автор + Муж./Жен. роли` поддерживается только в `Live API`; в `Flash TTS` он автоматически переводится в `duo`.
- Если приложение запускается из исходников, перед использованием новых TTS-возможностей стоит обновить зависимости из `requirements.txt`.
- Breaking changes для этого релиза не выделены.

## Suggested Assets

- `dist/translatorFork-translator.exe`
- `dist/translatorFork-full.exe`
- `translatorFork_MOD-v10.5.16.zip`
- `translatorFork_MOD-source-v10.5.16.zip`
