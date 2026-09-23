/*
 * Замена перевода глав на Rulate из файла Rulate MD (translatorFork).
 *
 * Зачем. Загрузчик MD на tl.rulate.ru только добавляет новые главы, а текст
 * уже выложенных глав меняется по одной: «Редактировать перевод» → правка →
 * «Сохранить». После инструмента «Системные окна» так пришлось бы открыть
 * сотни глав. Скрипт делает то же самое сам, глава за главой.
 *
 * Как запустить. Открыть страницу книги на tl.rulate.ru под своим логином,
 * вставить этот файл целиком в консоль разработчика (F12 → Console, в Chrome
 * сначала набрать allow pasting) и нажать Enter. Справа внизу появится
 * панель. Во встроенном браузере приложения Claude скрипт запускает Claude.
 *
 * Что делает панель.
 *   1. Читает MD, собранный конвертером «EPUB -> Rulate MD»: главы узнаются
 *      по заголовкам « # [Название :|: Порядок :|: Платность :|: Том]».
 *   2. «Проверить» только читает сайт: сопоставляет главы файла с главами
 *      книги по названию, открывает каждую и сверяет текст по буквам и цифрам.
 *      Глава, где на сайте правили сам текст, по умолчанию не трогается.
 *   3. «Заменить» идёт по одной главе с паузами: старый текст кладёт
 *      в резервную копию этого браузера (IndexedDB), сохраняет новый и
 *      проверяет ответ сайта (рамки на месте, буквы совпадают). На первой
 *      ошибке останавливается.
 *   4. «Вернуть как было» отправляет старый текст из резервной копии.
 *
 * Формат отправки повторяет режим с выключенным визуальным редактором: строка
 * файла становится строкой перевода, HTML рамок проходит как есть (так сайт
 * принимает вставленный HTML, см. https://tl.rulate.ru/blog/23691). При
 * включённом редакторе сайт понял бы переводы строк иначе, поэтому скрипт
 * тогда останавливается.
 *
 * Чистые функции выше строки «браузер» проверяются в Node:
 * tests/js/rulate_replace_chapters.test.js.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.RulateReplace = api;
    api.mount(root.document);
  }
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  // --- файл MD --------------------------------------------------------------

  const HEADER_RE = /^ ?# \[(.*)\][ \t]*$/;

  /** Главы файла Rulate MD: название, поля заголовка и текст. */
  function parseMarkdown(text) {
    const chapters = [];
    let current = null;
    for (const line of String(text).replace(/^\uFEFF/, '').split(/\r?\n/)) {
      const header = HEADER_RE.exec(line);
      if (header) {
        const fields = header[1].split(':|:').map((field) => field.trim());
        current = {
          index: chapters.length,
          title: fields[0] || '',
          order: fields[1] || '',
          paid: fields[2] || '',
          volume: fields[3] || '',
          lines: [],
        };
        chapters.push(current);
      } else if (current) {
        current.lines.push(line);
      }
    }
    return chapters.map(({ lines, ...chapter }) => ({
      ...chapter,
      body: lines.join('\n').replace(/^\s*\n/, '').replace(/\s+$/, ''),
    }));
  }

  /** Ключ названия для сопоставления: пробелы, регистр, ё. */
  function normalizeTitle(title) {
    return decodeEntities(String(title || ''))
      .normalize('NFC')
      .replace(/[\u00A0\u2000-\u200B\u202F\u205F\u3000]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase()
      .replace(/ё/g, 'е');
  }

  /**
   * Пары «глава файла — глава сайта» по названию. Одинаковые названия
   * разбираются по порядку. outOfOrder — сколько раз порядок глав на сайте
   * пошёл назад относительно файла.
   */
  function matchChapters(fileChapters, siteChapters) {
    const byTitle = new Map();
    siteChapters.forEach((chapter, position) => {
      const key = normalizeTitle(chapter.title);
      if (!byTitle.has(key)) byTitle.set(key, []);
      byTitle.get(key).push({ ...chapter, position });
    });
    let lastPosition = -1;
    let outOfOrder = 0;
    const pairs = fileChapters.map((chapter) => {
      const queue = byTitle.get(normalizeTitle(chapter.title));
      const site = queue && queue.length ? queue.shift() : null;
      if (site) {
        if (site.position < lastPosition) outOfOrder += 1;
        lastPosition = site.position;
      }
      return { file: chapter, site };
    });
    return { pairs, outOfOrder };
  }

  // --- текст для сайта ------------------------------------------------------

  const FRAME_LINE_RE = /^\s*<div\b[^>]*\bdata-sys="[^"]*"/i;
  // Сборка EPUB пересобирает главы через BeautifulSoup, и значение с двойными
  // кавычками внутри оказывается в одинарных.
  const SOURCE_ATTR_RE = /\s*\bdata-sys-orig=(?:"[^"]*"|'[^']*')/gi;
  const BREAK_RE = /^(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$/;

  function isFrameLine(line) {
    return FRAME_LINE_RE.test(line);
  }

  function countFrameLines(body) {
    return String(body).split('\n').filter(isFrameLine).length;
  }

  function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /**
   * Текст главы в том виде, в каком его принимает поле перевода при
   * выключенном редакторе: строка на строку, рамка одной строкой без
   * атрибута с оригиналом, разделитель «***» — линией, как делал загрузчик
   * MD, остальной текст экранирован.
   */
  function buildBody(markdownBody) {
    const out = [];
    for (const raw of String(markdownBody).split('\n')) {
      const line = raw.trim();
      if (!line) continue;
      if (isFrameLine(line)) out.push(line.replace(SOURCE_ATTR_RE, ''));
      else if (BREAK_RE.test(line)) out.push('<hr>');
      else out.push(escapeHtml(line));
    }
    return out.join('\n');
  }

  /** Так текст выглядит на сайте после сохранения: перенос строки = <br>. */
  function bodyAsHtml(body) {
    return String(body).split('\n').join('<br>\n');
  }

  /**
   * Старый HTML перевода для повторной отправки. Сайт сам ставит <br> перед
   * каждым переводом строки, поэтому уже стоящие там <br> убираются.
   */
  function restoreBody(oldHtml) {
    return String(oldHtml).replace(/\r\n?/g, '\n').replace(/<br\s*\/?>[ \t]*\n/gi, '\n');
  }

  // --- сверка ---------------------------------------------------------------

  const ENTITIES = {
    amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00A0',
    laquo: '«', raquo: '»', mdash: '—', ndash: '–', hellip: '…',
  };
  const BLOCK_TAG_RE = /<\/?(?:p|div|li|ol|ul|hr|h[1-6]|blockquote|tr|table|center)\b[^>]*>|<br\s*\/?>/gi;
  const LIST_MARK_RE = /^(?:\d+[.)]|[-+*])\s+/;
  const LETTER_RE = /[\p{L}\p{N}]/u;

  function decodeEntities(text) {
    return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (match, name) => {
      if (name[0] === '#') {
        const hex = name[1] === 'x' || name[1] === 'X';
        const code = parseInt(name.slice(hex ? 2 : 1), hex ? 16 : 10);
        return code > 0 && code <= 0x10FFFF ? String.fromCodePoint(code) : match;
      }
      const value = ENTITIES[name.toLowerCase()];
      return value === undefined ? match : value;
    });
  }

  /** Видимый текст HTML построчно: <br> и блоки — переводы строки. */
  function htmlToPlain(html) {
    const text = decodeEntities(String(html).replace(BLOCK_TAG_RE, '\n').replace(/<[^>]*>/g, ''));
    return text
      .split('\n')
      .map((line) => line.replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .join('\n');
  }

  /**
   * Только буквы и цифры, в нижнем регистре, без маркеров списка в начале
   * строки: так сравниваются тексты, отличающиеся лишь оформлением (скобки
   * системных строк, рамки, списки и курсив, которые наделал загрузчик MD).
   */
  function lettersIndex(plain) {
    const chars = [];
    const positions = [];
    let offset = 0;
    for (const line of String(plain).normalize('NFC').split('\n')) {
      const marker = LIST_MARK_RE.exec(line);
      let position = offset + (marker ? marker[0].length : 0);
      for (const char of Array.from(marker ? line.slice(marker[0].length) : line)) {
        if (LETTER_RE.test(char)) {
          chars.push(char.toLowerCase().replace('ё', 'е'));
          positions.push(position);
        }
        position += char.length;
      }
      offset += line.length + 1;
    }
    return { chars, positions };
  }

  function lettersKey(plain) {
    return lettersIndex(plain).chars.join('');
  }

  /** Отрывки вокруг первого расхождения букв, чтобы было видно, что правили. */
  function describeDifference(sitePlain, filePlain) {
    const site = lettersIndex(sitePlain);
    const file = lettersIndex(filePlain);
    let index = 0;
    while (index < site.chars.length && index < file.chars.length && site.chars[index] === file.chars[index]) {
      index += 1;
    }
    const around = (plain, positions) => {
      const at = index < positions.length ? positions[index] : plain.length;
      return plain.slice(Math.max(0, at - 40), at + 40).replace(/\n/g, ' ⏎ ');
    };
    return { site: around(sitePlain, site.positions), file: around(filePlain, file.positions) };
  }

  /** Стили рамок в HTML: div со стилем border-left, без пробелов и регистра. */
  function frameStyles(html) {
    const re = /<div\b[^>]*\bstyle\s*=\s*("[^"]*"|'[^']*')/gi;
    const styles = [];
    let match;
    while ((match = re.exec(String(html))) !== null) {
      if (/border-left(?:-[a-z]+)?\s*:/i.test(match[1])) {
        styles.push(match[1].slice(1, -1).toLowerCase().replace(/\s+/g, '').replace(/;$/, ''));
      }
    }
    return styles;
  }

  /** Рамки в сохранённом HTML. */
  function frameCountInHtml(html) {
    return frameStyles(html).length;
  }

  /** Сравнить текст главы на сайте с главой файла. */
  function compareChapter(siteHtml, fileBody) {
    const fileHtml = bodyAsHtml(buildBody(fileBody));
    const sitePlain = htmlToPlain(siteHtml);
    const filePlain = htmlToPlain(fileHtml);
    const sameLetters = lettersKey(sitePlain) === lettersKey(filePlain);
    return {
      sameLetters,
      samePlain: sitePlain === filePlain,
      sameFrames: frameStyles(siteHtml).join('|') === frameStyles(fileHtml).join('|'),
      siteFrames: frameCountInHtml(siteHtml),
      fileFrames: countFrameLines(fileBody),
      difference: sameLetters ? null : describeDifference(sitePlain, filePlain),
    };
  }

  /**
   * Что делать с главой. frames — режим «только главы с рамками»,
   * force — заменять и там, где на сайте правили текст.
   */
  function decide(pair, comparison, { frames, force }) {
    if (!pair.site) return 'missing';
    if (frames && comparison.fileFrames === 0) return 'skip';
    if (frames && comparison.siteFrames >= comparison.fileFrames) return 'done';
    if (!frames && comparison.samePlain && comparison.sameFrames) return 'same';
    if (frames && !comparison.sameLetters && !force) return 'differs';
    return 'replace';
  }

  /** Проверка ответа сайта после сохранения; пустая строка — всё в порядке. */
  function verifySaved(savedHtml, body) {
    const expected = countFrameLines(body);
    const frames = frameCountInHtml(savedHtml);
    if (frames < expected) return `сайт сохранил рамок ${frames} из ${expected}`;
    if (lettersKey(htmlToPlain(savedHtml)) !== lettersKey(htmlToPlain(bodyAsHtml(body)))) {
      return 'текст на сайте после сохранения не совпадает с файлом';
    }
    return '';
  }

  // --- браузер: сайт --------------------------------------------------------

  const PAUSE_READ_MS = 250;
  const PAUSE_WRITE_MS = 800;
  const ATTEMPTS = 3;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function csrfToken(doc) {
    const meta = doc.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) return meta.getAttribute('content');
    const input = doc.querySelector('input[name="csrf_token"], input[name="YII_CSRF_TOKEN"]');
    return input ? input.value : '';
  }

  /**
   * Запрос с повторами при сбоях сети, 429, 5xx и странице защиты от DDoS
   * вместо JSON. Правка перевода по tr_id идемпотентна, повтор безопасен.
   */
  async function request(url, options, expect) {
    let lastError = null;
    for (let attempt = 0; attempt < ATTEMPTS; attempt += 1) {
      if (attempt) await sleep(3000 * attempt);
      let response;
      let text;
      try {
        response = await fetch(url, { credentials: 'same-origin', ...options });
        text = await response.text();
      } catch (error) {
        lastError = new Error(`нет связи с сайтом: ${error.message}`);
        continue;
      }
      if (response.status === 429 || response.status >= 500) {
        lastError = new Error(`сайт ответил HTTP ${response.status}`);
        continue;
      }
      if (expect === 'json') {
        try {
          return { status: response.status, data: JSON.parse(text) };
        } catch (error) {
          lastError = new Error(`сайт ответил не JSON (HTTP ${response.status})`);
          continue;
        }
      }
      if (!response.ok) throw new Error(`сайт ответил HTTP ${response.status}`);
      return { status: response.status, text };
    }
    throw lastError;
  }

  async function fetchSiteChapters(bookId) {
    const { data } = await request(`/book/${bookId}/chapters`, { headers: { Accept: 'application/json' } }, 'json');
    if (!Array.isArray(data)) throw new Error('список глав книги пришёл в неожиданном виде');
    return data.map((chapter) => ({ id: String(chapter.id), title: String(chapter.title || '') }));
  }

  /** Страница главы: фрагмент, ваша версия перевода, её HTML, режим редактора. */
  async function readChapter(bookId, chapterId) {
    const { text } = await request(`/book/${bookId}/${chapterId}`, {}, 'html');
    const doc = new DOMParser().parseFromString(text, 'text/html');
    const user = /new CUser\(\{\s*id:\s*(\d+)/.exec(text);
    const status = doc.getElementById('editorStatus');
    const state = {
      chapterId: String(chapterId),
      userId: user ? user[1] : '',
      editorOn: status ? status.getAttribute('value') === '1' : false,
      token: csrfToken(doc),
    };
    if (!doc.getElementById('Tr') || !state.userId || state.userId === '0') {
      throw new Error('сайт отдал страницу главы без перевода: вы не вошли на сайт или сработала защита от DDoS');
    }
    const rows = doc.querySelectorAll('#Tr tr[id^="o"]');
    if (rows.length !== 1) {
      return { ...state, problem: rows.length ? `фрагментов ${rows.length}, скрипт меняет главы из одного` : 'нет фрагментов перевода' };
    }
    const mine = Array.from(rows[0].querySelectorAll('td.t div[id^="t"]')).filter(
      (div) => /^t\d+$/.test(div.id) && div.classList.contains(`u${state.userId}`),
    );
    if (!mine.length) return { ...state, problem: 'нет вашей версии перевода' };
    if (mine.length > 1) return { ...state, problem: `ваших версий перевода ${mine.length}` };
    const textNode = mine[0].querySelector('.text');
    return { ...state, origId: rows[0].id.slice(1), trId: mine[0].id.slice(1), html: textNode ? textNode.innerHTML : '' };
  }

  /** То же, что «Сохранить» у версии перевода. Возвращает сохранённый HTML. */
  async function saveTranslation(bookId, state, body) {
    const form = new FormData();
    form.append('Translation[body]', body);
    form.append('ajax', '1');
    form.append('csrf_token', state.token);
    const url = `/book/${bookId}/${state.chapterId}/${state.origId}/translate?tr_id=${state.trId}`;
    const { status, data } = await request(url, {
      method: 'POST',
      body: form,
      headers: {
        'X-CSRF-Token': state.token,
        'X-Requested-With': 'XMLHttpRequest',
        Accept: 'application/json, text/javascript, */*; q=0.01',
      },
    }, 'json');
    if (!data || data.error) throw new Error(data && data.error ? String(data.error) : `сайт ответил HTTP ${status}`);
    const doc = new DOMParser().parseFromString(String(data.text || ''), 'text/html');
    const div = Array.from(doc.querySelectorAll('div[id^="t"]')).find((node) => /^t\d+$/.test(node.id));
    const textNode = div ? div.querySelector('.text') : null;
    return { html: textNode ? textNode.innerHTML : '', trId: div ? div.id.slice(1) : state.trId };
  }

  // --- браузер: резервная копия ---------------------------------------------

  const DB_NAME = 'translatorfork-rulate-replace';

  function idb(requestObject) {
    return new Promise((resolve, reject) => {
      requestObject.onsuccess = () => resolve(requestObject.result);
      requestObject.onerror = () => reject(requestObject.error);
    });
  }

  async function openBackupDb() {
    const opening = indexedDB.open(DB_NAME, 1);
    opening.onupgradeneeded = () => {
      const db = opening.result;
      db.createObjectStore('backups', { keyPath: 'key' }).createIndex('run', 'runId');
      db.createObjectStore('runs', { keyPath: 'runId' });
    };
    return idb(opening);
  }

  async function withStore(name, mode, action) {
    const db = await openBackupDb();
    try {
      return await action(db.transaction(name, mode).objectStore(name));
    } finally {
      db.close();
    }
  }

  const backups = {
    async save(run, entry) {
      await withStore('backups', 'readwrite', (store) => idb(store.put({
        ...entry, key: `${run.runId}:${entry.chapterId}`, runId: run.runId, bookId: run.bookId, savedAt: new Date().toISOString(),
      })));
      await withStore('runs', 'readwrite', (store) => idb(store.put(run)));
    },
    async lastRun(bookId) {
      const runs = await withStore('runs', 'readonly', (store) => idb(store.getAll()));
      return runs.filter((run) => run.bookId === bookId && run.count > 0).sort((a, b) => b.runId - a.runId)[0] || null;
    },
    async entries(runId) {
      const rows = await withStore('backups', 'readonly', (store) => idb(store.index('run').getAll(runId)));
      return rows.sort((a, b) => a.order - b.order);
    },
  };

  // --- браузер: панель ------------------------------------------------------

  const HOST_ID = 'translatorfork-rulate-replace';

  const STYLE = `
    :host { all: initial; }
    .panel { position: fixed; right: 16px; bottom: 16px; z-index: 2147483000; width: min(460px, calc(100vw - 32px));
      max-height: calc(100vh - 32px); display: flex; flex-direction: column; box-sizing: border-box;
      font: 13px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--fg);
      background: var(--bg); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 12px 32px rgba(0,0,0,.28); }
    .panel { --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --line: #d0d7de; --soft: #f6f8fa; --accent: #0969da;
      --accent-fg: #ffffff; --ok: #1a7f37; --warn: #9a6700; --bad: #cf222e; }
    .panel.dark { --bg: #1c2128; --fg: #e6edf3; --muted: #9da7b3; --line: #3d444d; --soft: #262c36; --accent: #4493f8;
      --accent-fg: #0d1117; --ok: #3fb950; --warn: #d29922; --bad: #f85149; }
    header { display: flex; align-items: center; gap: 8px; padding: 10px 12px 8px 14px; border-bottom: 1px solid var(--line); }
    header h2 { flex: 1; margin: 0; font-size: 14px; font-weight: 600; }
    .icon { border: 0; background: transparent; color: var(--muted); font-size: 16px; line-height: 1; padding: 4px 6px;
      border-radius: 6px; cursor: pointer; }
    .icon:hover { background: var(--soft); color: var(--fg); }
    .body { padding: 10px 14px 12px; overflow: auto; display: flex; flex-direction: column; gap: 10px; }
    .panel.collapsed .body { display: none; }
    .muted { color: var(--muted); }
    .drop { border: 1px dashed var(--line); border-radius: 8px; padding: 10px; background: var(--soft); }
    .drop.over { border-color: var(--accent); }
    .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    label.check { display: flex; gap: 8px; align-items: flex-start; }
    label.check input { margin-top: 3px; }
    button.btn { font: inherit; border-radius: 8px; padding: 6px 12px; cursor: pointer; border: 1px solid var(--line);
      background: var(--soft); color: var(--fg); }
    button.btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-fg); font-weight: 600; }
    button.btn.danger { color: var(--bad); }
    button.btn:disabled { opacity: .45; cursor: default; }
    progress { width: 100%; height: 8px; }
    .summary { padding: 8px 10px; border-radius: 8px; background: var(--soft); }
    .summary b { font-weight: 600; }
    ul.list, ol.log { margin: 0; padding: 0 0 0 18px; max-height: 180px; overflow: auto; }
    ol.log { max-height: 220px; }
    li { margin: 2px 0; }
    li.ok::marker { color: var(--ok); }
    li.warn::marker, .warn { color: var(--warn); }
    li.bad::marker, .bad { color: var(--bad); }
    a { color: var(--accent); }
    .quote { display: block; font-size: 12px; color: var(--muted); }
    [hidden] { display: none !important; }
  `;

  const MARKUP = `
    <section class="panel" role="dialog" aria-label="Замена перевода из MD">
      <header>
        <h2>Замена перевода из MD</h2>
        <button class="icon" data-act="collapse" title="Свернуть" aria-label="Свернуть">▁</button>
        <button class="icon" data-act="close" title="Закрыть" aria-label="Закрыть">✕</button>
      </header>
      <div class="body">
        <div class="muted" data-el="book"></div>
        <div class="drop" data-el="drop">
          <div class="row">
            <button class="btn" data-act="pick">Выбрать файл MD</button>
            <span class="muted">или перетащите его сюда</span>
          </div>
          <div data-el="file" class="muted" style="margin-top:6px">Файл не выбран.</div>
          <input type="file" accept=".md,.txt,.json" data-el="input" hidden>
        </div>
        <label class="check"><input type="checkbox" data-el="frames" checked>
          <span>Только главы с рамками</span></label>
        <label class="check"><input type="checkbox" data-el="force">
          <span>Заменять и главы, где текст на сайте правили после загрузки<br>
          <span class="muted">Правки на сайте пропадут.</span></span></label>
        <div class="row">
          <button class="btn primary" data-act="check" disabled>Проверить</button>
          <button class="btn" data-act="stop" hidden>Стоп</button>
        </div>
        <div data-el="progress" hidden><progress max="1" value="0"></progress>
          <div class="muted" data-el="progress-text" aria-live="polite"></div></div>
        <div class="summary" data-el="summary" hidden></div>
        <ul class="list" data-el="issues" hidden></ul>
        <div class="row" data-el="actions" hidden>
          <button class="btn" data-act="try">Заменить одну для пробы</button>
          <button class="btn primary" data-act="run">Заменить все</button>
        </div>
        <ol class="log" data-el="log" hidden></ol>
        <div class="row" data-el="backup" hidden>
          <span class="muted" data-el="backup-text"></span>
          <button class="btn" data-act="download">Скачать копию</button>
          <button class="btn danger" data-act="restore">Вернуть как было</button>
        </div>
      </div>
    </section>`;

  const STATUS_TEXT = {
    replace: 'заменить',
    done: 'уже с рамками',
    same: 'без изменений',
    differs: 'текст на сайте правили',
    missing: 'нет на сайте',
    problem: 'не подходит',
    skip: 'в файле нет рамок',
    replaced: 'заменена',
  };

  class Panel {
    constructor(shadow, doc, bookId) {
      this.doc = doc;
      this.win = doc.defaultView;
      this.bookId = bookId;
      this.file = null;
      this.backupFile = null;
      this.plan = null;
      this.busy = false;
      this.stopRequested = false;
      this.el = {};
      shadow.querySelectorAll('[data-el]').forEach((node) => { this.el[node.dataset.el] = node; });
      this.panel = shadow.querySelector('.panel');
      this.buttons = {};
      shadow.querySelectorAll('[data-act]').forEach((node) => {
        this.buttons[node.dataset.act] = node;
        node.addEventListener('click', () => this.onAction(node.dataset.act));
      });
      if (doc.body.classList.contains('dark-theme')) this.panel.classList.add('dark');
      const book = this.win.Book && this.win.Book.id === Number(bookId) ? this.win.Book.t_title : '';
      this.el.book.textContent = `Книга ${bookId}${book ? ` · ${book}` : ''}`;
      this.el.input.addEventListener('change', () => this.loadFile(this.el.input.files[0]));
      this.el.frames.addEventListener('change', () => {
        this.el.force.disabled = !this.el.frames.checked;
        this.invalidate();
      });
      this.el.force.addEventListener('change', () => this.invalidate());
      const drop = this.el.drop;
      drop.addEventListener('dragover', (event) => { event.preventDefault(); drop.classList.add('over'); });
      drop.addEventListener('dragleave', () => drop.classList.remove('over'));
      drop.addEventListener('drop', (event) => {
        event.preventDefault();
        drop.classList.remove('over');
        if (event.dataTransfer.files.length) this.loadFile(event.dataTransfer.files[0]);
      });
      this.refreshBackup();
    }

    onAction(action) {
      const handlers = {
        pick: () => this.el.input.click(),
        collapse: () => this.panel.classList.toggle('collapsed'),
        close: () => { if (!this.busy) this.doc.getElementById(HOST_ID).remove(); },
        check: () => this.check(),
        stop: () => { this.stopRequested = true; this.setProgressText('Останавливаюсь после текущей главы…'); },
        try: () => this.replace(1),
        run: () => this.replace(Infinity),
        download: () => this.download(),
        restore: () => this.restore(),
      };
      if (handlers[action]) handlers[action]();
    }

    async loadFile(file) {
      if (!file || this.busy) return;
      try {
        this.loadText(await file.text(), file.name);
      } catch (error) {
        this.file = null;
        this.buttons.check.disabled = true;
        this.el.file.textContent = `${file.name}: не прочитать (${error.message}).`;
      }
    }

    /** Загрузить MD (или JSON резервной копии) из строки; так же зовёт Claude. */
    loadText(text, name) {
      this.invalidate();
      if (/\.json$/i.test(name || '')) {
        const data = JSON.parse(text);
        if (!Array.isArray(data.entries)) throw new Error('это не резервная копия замены');
        this.backupFile = data;
        this.file = null;
        this.el.file.textContent = `Резервная копия: ${name}, глав ${data.entries.length}.`;
        this.buttons.check.disabled = true;
        this.showBackup(`В копии из файла глав: ${data.entries.length}.`, false);
        return;
      }
      if (this.backupFile) {
        this.backupFile = null;
        this.refreshBackup();
      }
      const chapters = parseMarkdown(text);
      this.file = { name: name || 'MD', chapters };
      const withFrames = chapters.filter((chapter) => countFrameLines(chapter.body) > 0).length;
      this.el.file.textContent = chapters.length
        ? `${this.file.name}: глав ${chapters.length}, с рамками ${withFrames}.`
        : `${this.file.name}: заголовков глав « # [Название :|: …]» не найдено.`;
      this.buttons.check.disabled = !chapters.length;
    }

    invalidate() {
      this.plan = null;
      this.el.summary.hidden = true;
      this.el.issues.hidden = true;
      this.el.actions.hidden = true;
    }

    setBusy(busy) {
      this.busy = busy;
      this.stopRequested = false;
      for (const name of ['check', 'try', 'run', 'restore', 'pick']) this.buttons[name].disabled = busy;
      if (!busy) this.buttons.check.disabled = !this.file;
      this.buttons.stop.hidden = !busy;
      this.el.frames.disabled = busy;
      this.el.force.disabled = busy || !this.el.frames.checked;
      if (busy) this.el.progress.hidden = false;
    }

    setProgress(done, total, text) {
      const bar = this.el.progress.querySelector('progress');
      bar.max = Math.max(total, 1);
      bar.value = done;
      this.setProgressText(text);
    }

    setProgressText(text) {
      this.el['progress-text'].textContent = text;
    }

    log(kind, text, chapter) {
      const item = this.doc.createElement('li');
      item.className = kind;
      item.append(text);
      if (chapter) {
        item.append(' ');
        const link = this.doc.createElement('a');
        link.href = `/book/${this.bookId}/${chapter}/ready_new`;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = 'открыть';
        item.append(link);
      }
      this.el.log.hidden = false;
      this.el.log.append(item);
      while (this.el.log.children.length > 400) this.el.log.firstChild.remove();
      item.scrollIntoView({ block: 'nearest' });
    }

    options() {
      return { frames: this.el.frames.checked, force: this.el.force.checked };
    }

    async check() {
      if (!this.file || this.busy) return;
      this.setBusy(true);
      this.invalidate();
      try {
        this.setProgress(0, 1, 'Читаю список глав книги…');
        const site = await fetchSiteChapters(this.bookId);
        const { pairs, outOfOrder } = matchChapters(this.file.chapters, site);
        const options = this.options();
        const wanted = pairs.filter((pair) => !options.frames || countFrameLines(pair.file.body) > 0);
        const items = [];
        let editorOn = false;
        for (let index = 0; index < wanted.length; index += 1) {
          if (this.stopRequested) break;
          const pair = wanted[index];
          this.setProgress(index, wanted.length, `Проверяю ${index + 1} из ${wanted.length}: ${pair.file.title}`);
          if (!pair.site) {
            items.push({ pair, status: 'missing' });
            continue;
          }
          let state;
          try {
            state = await readChapter(this.bookId, pair.site.id);
          } catch (error) {
            items.push({ pair, status: 'problem', reason: error.message });
            continue;
          }
          editorOn = editorOn || state.editorOn;
          if (state.problem) {
            items.push({ pair, status: 'problem', reason: state.problem });
          } else {
            const comparison = compareChapter(state.html, pair.file.body);
            items.push({ pair, comparison, status: decide(pair, comparison, options) });
          }
          await sleep(PAUSE_READ_MS);
        }
        this.plan = { items, options, stopped: this.stopRequested, outOfOrder, editorOn, siteCount: site.length };
        this.setProgress(wanted.length, wanted.length, this.stopRequested ? 'Проверка остановлена.' : 'Проверка закончена.');
        this.showPlan();
      } catch (error) {
        this.setProgressText(`Ошибка: ${error.message}`);
      } finally {
        this.setBusy(false);
        this.el.actions.hidden = !this.plan || !this.toReplace().length || this.plan.editorOn;
      }
    }

    toReplace() {
      return this.plan ? this.plan.items.filter((item) => item.status === 'replace') : [];
    }

    showPlan() {
      const counts = {};
      for (const item of this.plan.items) counts[item.status] = (counts[item.status] || 0) + 1;
      const parts = Object.keys(STATUS_TEXT)
        .filter((status) => counts[status])
        .map((status) => `${STATUS_TEXT[status]}: <b>${counts[status]}</b>`);
      const notes = [];
      if (this.plan.stopped) notes.push('Проверены не все главы.');
      if (this.plan.outOfOrder) notes.push(`Порядок глав на сайте и в файле расходится в ${this.plan.outOfOrder} местах; главы сопоставлены по названию.`);
      if (this.plan.editorOn) notes.push('<span class="bad">На сайте включён визуальный редактор. Откройте любую главу, нажмите «Редактировать перевод» → «отключить редактор» и проверьте снова.</span>');
      this.el.summary.innerHTML = `Глав на сайте ${this.plan.siteCount}. ${parts.join(' · ') || 'Подходящих глав нет.'}`
        + (notes.length ? `<br>${notes.join('<br>')}` : '');
      this.el.summary.hidden = false;
      this.buttons.run.textContent = `Заменить все (${this.toReplace().length})`;
      const issues = this.plan.items.filter((item) => !['replace', 'done', 'same'].includes(item.status)).slice(0, 60);
      this.el.issues.textContent = '';
      for (const item of issues) {
        const li = this.doc.createElement('li');
        li.className = item.status === 'missing' ? 'warn' : 'bad';
        li.append(`${item.pair.file.title}: ${STATUS_TEXT[item.status]}${item.reason ? ` (${item.reason})` : ''}`);
        if (item.pair.site) {
          li.append(' ');
          const link = this.doc.createElement('a');
          link.href = `/book/${this.bookId}/${item.pair.site.id}`;
          link.target = '_blank';
          link.rel = 'noopener';
          link.textContent = 'глава';
          li.append(link);
        }
        if (item.comparison && item.comparison.difference) {
          const quote = this.doc.createElement('span');
          quote.className = 'quote';
          quote.textContent = `сайт: …${item.comparison.difference.site}… | файл: …${item.comparison.difference.file}…`;
          li.append(quote);
        }
        this.el.issues.append(li);
      }
      this.el.issues.hidden = !issues.length;
    }

    async replace(limit) {
      const items = this.toReplace();
      if (!items.length || this.busy) return;
      const count = Math.min(limit, items.length);
      const question = count === 1
        ? `Заменить перевод главы «${items[0].pair.file.title}»? Старый текст сохранится в резервной копии этого браузера.`
        : `Заменить перевод в ${count} главах? Старый текст сохранится в резервной копии этого браузера.`;
      if (!this.win.confirm(question)) return;
      this.setBusy(true);
      const options = this.plan.options;
      const run = { runId: Date.now(), bookId: this.bookId, startedAt: new Date().toISOString(), file: this.file.name, count: 0 };
      let done = 0;
      try {
        for (const item of items.slice(0, count)) {
          if (this.stopRequested) break;
          const { file, site } = item.pair;
          this.setProgress(done, count, `Заменяю ${done + 1} из ${count}: ${file.title}`);
          const state = await readChapter(this.bookId, site.id);
          if (state.editorOn) {
            this.log('bad', 'На сайте включён визуальный редактор, замена остановлена.');
            break;
          }
          if (state.problem) {
            item.status = 'problem';
            this.log('warn', `${file.title}: пропущена, ${state.problem}.`, site.id);
            continue;
          }
          const comparison = compareChapter(state.html, file.body);
          const status = decide(item.pair, comparison, options);
          if (status !== 'replace') {
            item.status = status;
            this.log('warn', `${file.title}: пропущена, ${STATUS_TEXT[status]}.`, site.id);
            continue;
          }
          await backups.save(run, {
            chapterId: site.id, title: file.title, origId: state.origId, trId: state.trId, oldHtml: state.html, order: done,
          });
          const body = buildBody(file.body);
          let saved;
          try {
            saved = await saveTranslation(this.bookId, state, body);
          } catch (error) {
            this.log('bad', `${file.title}: сайт не принял текст (${error.message}). Замена остановлена.`, site.id);
            break;
          }
          run.count += 1;
          await backups.save(run, {
            chapterId: site.id, title: file.title, origId: state.origId, trId: saved.trId, oldHtml: state.html, order: done,
          });
          done += 1;
          item.status = 'replaced';
          const problem = verifySaved(saved.html, body);
          if (problem) {
            this.log('bad', `${file.title}: ${problem}. Замена остановлена, эту главу можно вернуть кнопкой «Вернуть как было».`, site.id);
            break;
          }
          this.log('ok', `${file.title}: заменена, рамок ${comparison.fileFrames}.`, site.id);
          await sleep(PAUSE_WRITE_MS);
        }
      } catch (error) {
        this.log('bad', `Ошибка: ${error.message}. Замена остановлена.`);
      } finally {
        this.setProgress(done, count, `Заменено глав: ${done}.`);
        this.setBusy(false);
        this.buttons.run.textContent = `Заменить все (${this.toReplace().length})`;
        this.el.actions.hidden = !this.toReplace().length;
        await this.refreshBackup();
      }
    }

    async refreshBackup() {
      try {
        const run = await backups.lastRun(this.bookId);
        this.lastBackup = run;
        if (run) this.showBackup(`Копия последней замены: глав ${run.count}, ${new Date(run.runId).toLocaleString()}.`, true);
      } catch (error) {
        this.showBackup(`Резервная копия недоступна: ${error.message}`, false);
      }
    }

    showBackup(text, canDownload) {
      this.el['backup-text'].textContent = text;
      this.buttons.download.hidden = !canDownload;
      this.el.backup.hidden = false;
    }

    async backupEntries() {
      if (this.backupFile) return this.backupFile.entries;
      return this.lastBackup ? backups.entries(this.lastBackup.runId) : [];
    }

    async download() {
      const entries = await this.backupEntries();
      const data = JSON.stringify({ bookId: this.bookId, runId: this.lastBackup && this.lastBackup.runId, entries }, null, 1);
      const link = this.doc.createElement('a');
      link.href = URL.createObjectURL(new Blob([data], { type: 'application/json' }));
      link.download = `rulate-backup-${this.bookId}-${this.lastBackup ? this.lastBackup.runId : 'file'}.json`;
      this.doc.body.append(link);
      link.click();
      setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
    }

    async restore() {
      if (this.busy) return;
      const entries = (await this.backupEntries()).filter((entry) => String(entry.bookId || this.bookId) === this.bookId);
      if (!entries.length) {
        this.win.alert('В резервной копии нет глав этой книги.');
        return;
      }
      if (!this.win.confirm(`Вернуть прежний текст в ${entries.length} главах?`)) return;
      this.setBusy(true);
      let done = 0;
      try {
        for (const entry of entries) {
          if (this.stopRequested) break;
          this.setProgress(done, entries.length, `Возвращаю ${done + 1} из ${entries.length}: ${entry.title}`);
          const state = await readChapter(this.bookId, entry.chapterId);
          if (state.problem || state.editorOn) {
            this.log('bad', `${entry.title}: не вернуть, ${state.problem || 'включён визуальный редактор'}.`, entry.chapterId);
            continue;
          }
          const saved = await saveTranslation(this.bookId, state, restoreBody(entry.oldHtml));
          const same = lettersKey(htmlToPlain(saved.html)) === lettersKey(htmlToPlain(entry.oldHtml));
          this.log(same ? 'ok' : 'bad', `${entry.title}: ${same ? 'возвращена' : 'возвращена, но текст отличается от копии'}.`, entry.chapterId);
          done += 1;
          await sleep(PAUSE_WRITE_MS);
        }
      } catch (error) {
        this.log('bad', `Ошибка: ${error.message}. Возврат остановлен.`);
      } finally {
        this.setProgress(done, entries.length, `Возвращено глав: ${done}.`);
        this.setBusy(false);
        this.invalidate();
      }
    }
  }

  function mount(doc) {
    if (!doc) return null;
    const win = doc.defaultView;
    const book = /^\/book\/(\d+)/.exec(win.location.pathname);
    if (!/(^|\.)rulate\.ru$/.test(win.location.hostname) || !book) {
      win.alert('Откройте страницу книги на tl.rulate.ru и запустите скрипт ещё раз.');
      return null;
    }
    const previous = doc.getElementById(HOST_ID);
    if (previous) previous.remove();
    const host = doc.createElement('div');
    host.id = HOST_ID;
    doc.body.append(host);
    const shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = `<style>${STYLE}</style>${MARKUP}`;
    const panel = new Panel(shadow, doc, book[1]);
    host.panel = panel;
    return panel;
  }

  return {
    parseMarkdown,
    normalizeTitle,
    matchChapters,
    isFrameLine,
    countFrameLines,
    buildBody,
    bodyAsHtml,
    restoreBody,
    htmlToPlain,
    lettersKey,
    describeDifference,
    frameStyles,
    frameCountInHtml,
    compareChapter,
    decide,
    verifySaved,
    mount,
  };
}));
