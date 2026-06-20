# GitHub Release Draft

Suggested tag: `v10.5.20`
Suggested title: `translatorFork_MOD v10.5.20`
Range: `v10.5.19..HEAD` (`2026-05-08`)

## Release Body

## Что нового

- Добавлен CLI-слой для автоматизации переводчика: `translator_cli.py`, `translator_cli.bat` и документация `docs/user-guide/14-cli-agents.md`.
- Добавлены базовый preset промпта перевода и новые fandom-глоссарии: Naruto, Warhammer и Worm.
- В валидаторе улучшен переход из поиска непереведенных фрагментов к исправлению проблемных мест.
- В Gemini Reader обновлены TTS-сценарии, фильтры и обработка аудио.
- WorkAscii runtime получил обновленные настройки и более устойчивую работу с bridge.

## Исправления

- Исправлен выбор источника в setup flow.
- Исправлена обработка `null`-полей в глоссариях.
- Исправлена сборка чанков после потери memfs-состояния.
- Валидатор теперь повторяет chunk-запросы при временных ошибках.
- Детектор непереведенного текста игнорирует CJK-пунктуацию и лучше приоритизирует проблемные символы.
- Исправлен deep cleanup profile для EPUB.
- Разрешено смешивать пакетную обработку глав и чанкинг.

## Технические изменения

- Добавлены и обновлены регрессионные тесты для CLI, chunk assembler, setup source selection, untranslated fixer navigation, WorkAscii runtime, EPUB cleanup, Reader audio/manual script и sequential translation.
- Полный тестовый набор перед релизом: `247 passed`.
- Windows-сборки пересобраны локально через `build_release_dual.bat all`.

## Важно

- Это следующий релиз после `v10.5.19`, опубликованного `30 April 2026`.
- Breaking changes для этого релиза не выделены.
- Windows-ассеты подготовлены локально в `release-v10.5.20-upload`.
- Linux-ассет `translatorFork-translator` в текущем `dist` остался от предыдущей локальной сборки `30 April 2026`; для свежего Linux-бинаря нужно запускать `build_translator_linux.sh` в Linux-окружении.

## Suggested Assets

- `dist/translatorFork-translator.exe`
- `dist/translatorFork-full.exe`
- `translatorFork_MOD-v10.5.20.zip`
- `translatorFork_MOD-source-v10.5.20.zip`
