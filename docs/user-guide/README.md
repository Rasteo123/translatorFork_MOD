# Пользовательская документация

Эта папка содержит документацию для обычного пользователя приложения, а не для разработчика. Здесь описано, какой инструмент выбирать, что он делает, какие данные нужны на входе и какой результат вы получите на выходе.

## С чего начать

Если вы открыли программу впервые, читайте в таком порядке:

1. [01-start-here.md](./01-start-here.md)
2. [09-providers-models-keys.md](./09-providers-models-keys.md)
3. [02-epub-translator.md](./02-epub-translator.md)
4. [03-validator.md](./03-validator.md)

## Инструменты стартового меню

- [02-epub-translator.md](./02-epub-translator.md) — основной переводчик EPUB-проектов с импортом DOCX, TXT, Markdown, HTML и PDF.
- [03-validator.md](./03-validator.md) — проверка, ручная правка и доводка перевода.
- [04-glossary-manager.md](./04-glossary-manager.md) — работа с глоссариями, конфликтами и импортом терминов.
- [05-rulate-export.md](./05-rulate-export.md) — экспорт EPUB в Markdown для Rulate.
- [06-chapter-splitter.md](./06-chapter-splitter.md) — разбиение больших глав на части.
- [07-gemini-reader.md](./07-gemini-reader.md) — озвучивание EPUB и сборка MP3.
- [08-ranobelib-uploader.md](./08-ranobelib-uploader.md) — загрузка глав на RanobeLib и работа с Rulate.
- [13-prompt-benchmark.md](./13-prompt-benchmark.md) — сравнение промптов и моделей на одинаковых тестовых фрагментах.

## Общие темы

- [09-providers-models-keys.md](./09-providers-models-keys.md) — сервисы, модели, API-ключи и основные настройки качества/скорости.
- [10-projects-queue-and-history.md](./10-projects-queue-and-history.md) — проекты, очередь, история, резервные копии очереди и синхронизация.
- [11-proxy-and-network-issues.md](./11-proxy-and-network-issues.md) — прокси, геоблоки, браузерные режимы и частые сетевые проблемы.
- [12-common-workflows.md](./12-common-workflows.md) — готовые сценарии работы под разные задачи.
- [13-prompt-benchmark.md](./13-prompt-benchmark.md) — как проверять новые промпты, модели и настройки перед большим переводом.

## Как выбрать инструмент

- Если нужно перевести книгу или документ: [02-epub-translator.md](./02-epub-translator.md).
- Если перевод уже сделан и его надо вычитать: [03-validator.md](./03-validator.md).
- Если термины конфликтуют или нужен отдельный словарь: [04-glossary-manager.md](./04-glossary-manager.md).
- Если готовите главы для публикации на Rulate: [05-rulate-export.md](./05-rulate-export.md).
- Если глава слишком большая и её надо порезать на части: [06-chapter-splitter.md](./06-chapter-splitter.md).
- Если нужна озвучка в MP3: [07-gemini-reader.md](./07-gemini-reader.md).
- Если нужно отправить готовые главы на RanobeLib: [08-ranobelib-uploader.md](./08-ranobelib-uploader.md).
- Если нужно выбрать промпт или модель перед переводом: [13-prompt-benchmark.md](./13-prompt-benchmark.md).

## Что важно помнить

- Программа работает проектами. Лучше выделять отдельную папку под каждую книгу.
- Перевод, валидация, глоссарий и история связаны между собой через файлы проекта.
- Для части режимов нужны внешние сервисы, API-ключи, Chromium или `node`.
- Если сервисы недоступны из вашего региона, сначала настройте прокси: [11-proxy-and-network-issues.md](./11-proxy-and-network-issues.md).
