// Чистые функции скрипта замены перевода на Rulate (tools/rulate_replace_chapters.js).
// Запуск: node --test tests/js/rulate_replace_chapters.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const api = require(path.join(__dirname, '..', '..', 'tools', 'rulate_replace_chapters.js'));

const NBSP = String.fromCharCode(0xa0);
const BOM = String.fromCharCode(0xfeff);

const FRAME = '<div data-sys="notice" data-sys-orig=\'&lt;p class="no-indent"&gt;[Динь!]&lt;/p&gt;\' '
  + 'style="margin:16px 0;border:2px solid #b388ff;border-left:8px solid #b388ff;">'
  + 'Динь! Здравствуйте, носитель!<br/>Динь! Обнаружено избыточное количество систем</div>';

// Глава файла Rulate MD после инструмента «Системные окна».
const FILE_BODY = [
  'Пока Су Нянь пребывал в замешательстве, в его голове раздались сигналы:',
  FRAME,
  'Он огляделся.',
  '***',
  'Примечание: по правилам D& D кобольды похожи на драконов.',
  'Внимание:',
  '1. Запрещено шуметь.',
  '2. Запрещено оскорблять Его Величество».',
  '— И впрямь?',
].join('\n');

// Та же глава на сайте, загруженная MD-загрузчиком до рамок: Markdown, затем <br> на каждый перевод строки.
const SITE_HTML = [
  '<p>Пока Су Нянь пребывал в замешательстве, в его голове раздались сигналы:<br>',
  '[Динь! Здравствуйте, носитель!]<br>',
  '[Динь! Обнаружено избыточное количество систем]<br>',
  'Он огляделся.</p><br>',
  '<hr><br>',
  '<p>Примечание: по правилам D&amp; D кобольды похожи на драконов.<br>',
  'Внимание:</p><br>',
  '<ol><br>',
  '<li>Запрещено шуметь.</li><br>',
  '<li>Запрещено оскорблять Его Величество».<br>',
  '— И впрямь?</li></ol>',
].join('\n');

test('parseMarkdown reads headers with and without the leading space', () => {
  const text = `${BOM}мусор до первой главы\n # [Глава 1: «Начало» :|: :|: 1 :|: ]\r\nПервая строка.\r\nВторая.\r\n\r\n`
    + '# [Глава 2 :|: 7 :|: 0 :|: Том 1]\nТекст второй.\n';

  const chapters = api.parseMarkdown(text);

  assert.equal(chapters.length, 2);
  assert.deepEqual(chapters.map((chapter) => chapter.title), ['Глава 1: «Начало»', 'Глава 2']);
  assert.equal(chapters[0].body, 'Первая строка.\nВторая.');
  assert.equal(chapters[1].order, '7');
  assert.equal(chapters[1].paid, '0');
  assert.equal(chapters[1].volume, 'Том 1');
  assert.equal(chapters[1].body, 'Текст второй.');
});

test('normalizeTitle ignores spacing, case, ё and HTML entities', () => {
  assert.equal(
    api.normalizeTitle(`  Глава 5:${NBSP} «Ёлка»  &amp; ДРАКОН `),
    api.normalizeTitle('глава 5: «елка» & дракон'),
  );
});

test('matchChapters pairs by title, takes duplicates in order and counts order breaks', () => {
  const file = [{ title: 'Глава 1' }, { title: 'Интерлюдия' }, { title: 'Глава 3' }, { title: 'Интерлюдия' }, { title: 'Нет на сайте' }];
  const site = [
    { id: '10', title: 'Глава 1' },
    { id: '11', title: 'Интерлюдия' },
    { id: '13', title: 'Интерлюдия' },
    { id: '12', title: 'Глава 3' },
  ];

  const { pairs, outOfOrder } = api.matchChapters(file, site);

  assert.deepEqual(pairs.map((pair) => (pair.site ? pair.site.id : null)), ['10', '11', '12', '13', null]);
  assert.equal(outOfOrder, 1);
});

test('buildBody keeps the frame on one line without its source attribute', () => {
  const body = api.buildBody(FILE_BODY);
  const lines = body.split('\n');

  assert.equal(lines[1].startsWith('<div data-sys="notice" style="'), true);
  assert.equal(lines[1].includes('data-sys-orig'), false);
  assert.equal(lines[1].endsWith('</div>'), true);
  assert.equal(api.countFrameLines(body), 1);
});

test('buildBody drops the source attribute in double quotes too', () => {
  const line = '<div data-sys="status" data-sys-orig="&lt;p&gt;[Уровень: 3]&lt;/p&gt;&#10;" style="border-left:8px solid #4fc3f7;">Уровень: 3</div>';

  assert.equal(api.buildBody(line), '<div data-sys="status" style="border-left:8px solid #4fc3f7;">Уровень: 3</div>');
});

test('buildBody turns scene breaks into a line and escapes plain text', () => {
  const body = api.buildBody('Раз <b> & два\n\n***\n* * *\n---\nТри');

  assert.equal(body, 'Раз &lt;b&gt; &amp; два\n<hr>\n<hr>\n<hr>\nТри');
});

test('htmlToPlain splits on breaks and blocks and decodes entities', () => {
  assert.equal(
    api.htmlToPlain('<p>Раз&nbsp;и<br>\nдва</p><br>\n<hr><ol><li>три &amp; <b>четыре</b></li></ol>'),
    'Раз и\nдва\nтри & четыре',
  );
});

test('lettersKey ignores punctuation, brackets, list numbers, case and ё', () => {
  assert.equal(api.lettersKey('[Динь! Ёжик]\n1. Второй — пункт'), api.lettersKey('динь ежик\nВторой пункт'));
  assert.notEqual(api.lettersKey('Уровень 3'), api.lettersKey('Уровень 4'));
});

test('compareChapter sees only the frames as a difference from the site text', () => {
  const comparison = api.compareChapter(SITE_HTML, FILE_BODY);

  assert.equal(comparison.sameLetters, true);
  assert.equal(comparison.siteFrames, 0);
  assert.equal(comparison.fileFrames, 1);
  assert.equal(comparison.difference, null);
});

test('compareChapter shows where the site text was edited', () => {
  const edited = SITE_HTML.replace('Он огляделся.', 'Он осмотрелся.');

  const comparison = api.compareChapter(edited, FILE_BODY);

  assert.equal(comparison.sameLetters, false);
  assert.match(comparison.difference.site, /осмотрелся/);
  assert.match(comparison.difference.file, /огляделся/);
});

test('decide covers every status', () => {
  const pair = { file: { body: FILE_BODY }, site: { id: '1' } };
  const same = { sameLetters: true, samePlain: true, sameFrames: true, siteFrames: 0, fileFrames: 1 };
  const frames = { frames: true, force: false };

  assert.equal(api.decide({ ...pair, site: null }, same, frames), 'missing');
  assert.equal(api.decide(pair, same, frames), 'replace');
  assert.equal(api.decide(pair, { ...same, siteFrames: 1 }, frames), 'done');
  assert.equal(api.decide(pair, { ...same, fileFrames: 0 }, frames), 'skip');
  assert.equal(api.decide(pair, { ...same, sameLetters: false }, frames), 'differs');
  assert.equal(api.decide(pair, { ...same, sameLetters: false }, { frames: true, force: true }), 'replace');
  assert.equal(api.decide(pair, { ...same, siteFrames: 1 }, { frames: false, force: false }), 'same');
  assert.equal(api.decide(pair, { ...same, samePlain: false, siteFrames: 1 }, { frames: false, force: false }), 'replace');
  assert.equal(api.decide(pair, { ...same, sameFrames: false, siteFrames: 1 }, { frames: false, force: false }), 'replace');
});

test('frameCountInHtml counts divs with a left border, whatever the quoting', () => {
  const html = '<div style="border-left:8px solid red">a</div><div style=\'border-left-width: 8px\'>b</div>'
    + '<div style="border:1px solid">c</div><div class="x">d</div>';

  assert.equal(api.frameCountInHtml(html), 2);
});

test('verifySaved accepts what the site should store and rejects lost frames or text', () => {
  const body = api.buildBody(FILE_BODY);
  const stored = api.bodyAsHtml(body).replace(/ data-sys="notice"/, '');

  assert.equal(api.verifySaved(stored, body), '');
  assert.match(api.verifySaved(api.bodyAsHtml(body).replace(/style="[^"]*"/, ''), body), /рамок 0 из 1/);
  assert.match(api.verifySaved(stored.replace('Он огляделся.', ''), body), /не совпадает/);
});

test('restoreBody lets the site put back exactly the breaks it had', () => {
  const restored = api.restoreBody(SITE_HTML);

  assert.equal(restored.includes('<br>\n'), false);
  assert.equal(api.bodyAsHtml(restored).replace(/<br>\n/g, '\n'), restored);
  assert.equal(api.lettersKey(api.htmlToPlain(api.bodyAsHtml(restored))), api.lettersKey(api.htmlToPlain(SITE_HTML)));
});

test('frameStyles tells frames apart by their colours, not by spacing or case', () => {
  const site = '<div style="BORDER-LEFT: 8px solid #B388FF; color: #efe6ff;">a</div>';

  assert.deepEqual(api.frameStyles(site), api.frameStyles('<div data-sys="x" style="border-left:8px solid #b388ff;color:#efe6ff">a</div>'));
  assert.notDeepEqual(api.frameStyles(site), api.frameStyles('<div style="border-left:8px solid #4fc3f7;color:#efe6ff">a</div>'));
  assert.equal(api.compareChapter(api.bodyAsHtml(api.buildBody(FILE_BODY)), FILE_BODY).sameFrames, true);
});

test('buildBody undoes the Markdown escapes of the converter', () => {
  const body = api.buildBody('\\[Текущая благосклонность]: 80 (обожание)\nНик &lt;Shadow> вошёл.');

  assert.equal(body, '[Текущая благосклонность]: 80 (обожание)\nНик &lt;Shadow&gt; вошёл.');
});
