# GitHub Release Draft

Suggested tag: `v10.5.21`
Suggested title: `translatorFork_MOD v10.5.21`
Range: `v10.5.20..HEAD` (`2026-06-25`)

## Release Body

## Что нового

- Добавлен полноценный Rulate -> RanobeLib workflow для создания карточек: перенос метаданных, обложки, автора, жанров, тегов и ссылки на оригинал.
- Улучшен Qidian/Fanqie -> Rulate creator: отдельный cookie-профиль Rulate, нормализация жанров/тегов и более устойчивое заполнение формы.
- Добавлен TomatoNovelDownloader в bundled tools и сборочные сценарии.
- Добавлен переключатель единиц размера задач и улучшена упаковка batch preview.
- Добавлен автообновлятор, source updater и Windows one-dir/installer flow через Inno Setup.
- Улучшены системные уведомления Windows/macOS и единая обработка уведомлений в UI.

## Исправления

- Исправлено добавление нескольких жанров и тегов в RanobeLib uploader: выпадающий список больше не закрывается после первого выбранного элемента.
- Исправлена пересборка очереди после изменения опций.
- Исправлена обработка `null`-ключей сортировки в UI state.
- Исправлен source update на Windows: updater больше не блокируется confirm-диалогом выхода и ошибкой git identity.
- Исправлены Windows/macOS notification edge cases, включая AppUserModelID и silent tray notifications.
- Исправлены падения и нестабильность UI-тестов, task DB worker и glossary delegate flows.

## Технические изменения

- Обновлены build scripts и PyInstaller spec-файлы для bundled RanobeLib/Qidian/Tomato/Playwright runtime.
- Добавлен strict release metadata check для final semver и matching release notes.
- Добавлены регрессионные тесты для RanobeLib metadata, Qidian/Rulate catalog mapping, transient disconnect retry, task dirty tracking и release metadata.
- Локальные релизные проверки для подготовки этого релиза: `py_compile`, targeted pytest suites и strict release metadata check.

## Важно

- Это следующий релиз после `v10.5.20`.
- Windows-ассеты должны быть подготовлены локально и не загружены автоматически.
- Публикация GitHub release/tag не выполняется в рамках локальной подготовки файлов.

## Prepared Local Assets

- `dist/release-v10.5.21-upload/translatorFork-translator-v10.5.21-windows.zip`
- `dist/release-v10.5.21-upload/translatorFork-full-v10.5.21-windows.zip`
- `dist/release-v10.5.21-upload/translatorFork_MOD-source-v10.5.21.zip`
- `dist/release-v10.5.21-upload/SHA256SUMS.txt`
