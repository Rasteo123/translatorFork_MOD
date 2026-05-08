# CLI для автоматизации агентами

CLI запускается без открытия основного интерфейса и возвращает JSON в stdout. Внутренние логи приложения уходят в stderr, чтобы агент мог безопасно парсить результат.

## Быстрый старт

Проверить доступные провайдеры, модели и ключи:

```powershell
python -m gemini_translator.cli status
```

Собрать план очереди без API-вызовов:

```powershell
python -m gemini_translator.cli plan `
  --epub "C:\books\book.epub" `
  --project "C:\books\book_project" `
  --provider gemini `
  --model "Gemini 2.5 Flash Preview" `
  --mode batch `
  --chapters pending
```

Запустить headless-перевод:

```powershell
python -m gemini_translator.cli translate `
  --epub "C:\books\book.epub" `
  --project "C:\books\book_project" `
  --provider gemini `
  --model "Gemini 2.5 Flash Preview" `
  --all-keys `
  --workers 2 `
  --mode batch `
  --chapters pending `
  --timeout 21600
```

Собрать EPUB из готовых переводов:

```powershell
python -m gemini_translator.cli build-epub `
  --epub "C:\books\book.epub" `
  --project "C:\books\book_project" `
  --provider gemini `
  --output "C:\books\book_translated.epub"
```

На Windows можно использовать обертку:

```powershell
.\translator_cli.bat status
```

## Команды

- `status` показывает конфигурацию, провайдеры, модели, число сохраненных ключей и историю проектов.
- `plan` выбирает главы, строит задачи через тот же `TaskPreparer`, что и UI, и возвращает сводку очереди.
- `translate` строит очередь и запускает `TranslationEngine` в headless-режиме.
- `build-epub` берет `translation_map.json` и заменяет главы в исходном EPUB выбранными переводами.

## Выбор глав

- `--chapters pending` переводит только главы, которых еще нет в `translation_map.json`. Это режим по умолчанию.
- `--chapters all` пересобирает очередь по всем главам EPUB.
- `--chapters translated` выбирает только уже отмеченные в проекте главы.
- `--chapter` можно повторять. Значение работает как glob или подстрока внутреннего пути главы.
- `--offset` и `--limit` позволяют безопасно запускать книгу частями.

## Режимы очереди

- `--mode saved` использует сохраненные настройки приложения.
- `--mode single` создает отдельную задачу на каждую главу.
- `--mode batch` объединяет главы в пакеты по `--task-size`.
- `--mode chunk` режет крупные главы на чанки.
- `--mode sequential --splits N` запускает последовательные цепочки глав.

Дополнительные настройки можно передать через `--settings-json`; этот файл накладывается поверх собранных CLI-настроек последним шагом.
