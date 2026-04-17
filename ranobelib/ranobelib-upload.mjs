#!/usr/bin/env node

import { mkdir, readFile, appendFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { parseArgs } from "node:util";
import { chromium } from "playwright";

const API_BASE = "https://api.cdnlibs.org/api";
const SITE_BASE = "https://ranobelib.me";
const DEFAULT_BOOK_SLUG = "218360--baedeu-bon-beulleodeu";
const DEFAULT_SOURCE_DIR = "translated";
const DEFAULT_OUTPUT_DIR = path.join("output", "ranobelib");
const DEFAULT_PROFILE_DIR = path.join(DEFAULT_OUTPUT_DIR, "profile");

function parseCli() {
  const { values } = parseArgs({
    options: {
      slug: { type: "string", default: DEFAULT_BOOK_SLUG },
      "source-dir": { type: "string", default: DEFAULT_SOURCE_DIR },
      "output-dir": { type: "string", default: DEFAULT_OUTPUT_DIR },
      "profile-dir": { type: "string", default: DEFAULT_PROFILE_DIR },
      from: { type: "string" },
      to: { type: "string" },
      volume: { type: "string", default: "1" },
      "team-id": { type: "string" },
      "branch-id": { type: "string" },
      "publish-start": { type: "string" },
      "publish-interval-minutes": { type: "string", default: "0" },
      "use-titles": { type: "boolean", default: false },
      "titles-file": { type: "string", default: "chapter_titles.txt" },
      "dry-run": { type: "boolean", default: false },
      "headless-login": { type: "boolean", default: false },
      help: { type: "boolean", short: "h", default: false }
    }
  });

  if (values.help) {
    console.log(`Usage:
  npm run ranobelib:upload -- [options]

Options:
  --slug <slug>                     Book slug_url on RanobeLib
  --source-dir <dir>                Directory with chapter txt files
  --from <n>                        First chapter to upload
  --to <n>                          Last chapter to upload
  --volume <n>                      Volume number, default: 1
  --team-id <id>                    Override team id for upload
  --branch-id <id>                  Override branch id for upload
  --publish-start <iso-or-local>    Schedule first chapter, e.g. 2026-03-06T12:00
  --publish-interval-minutes <n>    Delay between scheduled chapters
  --use-titles                      Fill chapter names from chapter_titles.txt
  --titles-file <path>              Path to chapter titles file
  --dry-run                         Build payloads and print plan without uploading
  --headless-login                  Use headless browser for stored sessions only
`);
    process.exit(0);
  }

  return {
    slug: values.slug,
    sourceDir: values["source-dir"],
    outputDir: values["output-dir"],
    profileDir: values["profile-dir"],
    from: toInt(values.from),
    to: toInt(values.to),
    volume: String(values.volume),
    teamId: toInt(values["team-id"]),
    branchId: values["branch-id"] === undefined ? undefined : toNullableInt(values["branch-id"]),
    publishStart: values["publish-start"] ?? null,
    publishIntervalMinutes: toInt(values["publish-interval-minutes"]) ?? 0,
    useTitles: values["use-titles"],
    titlesFile: values["titles-file"],
    dryRun: values["dry-run"],
    headlessLogin: values["headless-login"]
  };
}

function toInt(value) {
  if (value === undefined || value === null || value === "") {
    return undefined;
  }
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isFinite(parsed)) {
    throw new Error(`Expected integer, got: ${value}`);
  }
  return parsed;
}

function toNullableInt(value) {
  if (value === "null") {
    return null;
  }
  return toInt(value);
}

function parseSlugId(slug) {
  const match = /^(\d+)--/.exec(slug);
  if (!match) {
    throw new Error(`Cannot parse manga id from slug: ${slug}`);
  }
  return Number.parseInt(match[1], 10);
}

function formatLocalDate(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:00`;
}

function parseDateInput(value) {
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) {
    throw new Error(`Cannot parse date: ${value}`);
  }
  return date;
}

function addMinutes(date, minutes) {
  return new Date(date.getTime() + minutes * 60_000);
}

async function apiFetch(pathname, { method = "GET", token, body } = {}) {
  const headers = {
    Accept: "application/json",
    Origin: SITE_BASE,
    Referer: `${SITE_BASE}/`,
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
  };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let payload;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const response = await fetch(`${API_BASE}${pathname}`, {
    method,
    headers,
    body: payload
  });

  const rawText = await response.text();
  let data;
  try {
    data = rawText ? JSON.parse(rawText) : null;
  } catch {
    data = rawText;
  }

  if (!response.ok) {
    const message =
      typeof data === "object" && data !== null
        ? JSON.stringify(data)
        : String(data);
    throw new Error(`${method} ${pathname} failed: ${response.status} ${message}`);
  }

  return data;
}

async function fetchExistingChapters(slug) {
  const response = await apiFetch(`/manga/${slug}/chapters`);
  return Array.isArray(response?.data) ? response.data : [];
}

async function fetchAuthMe(token) {
  const response = await apiFetch("/auth/me", { token });
  return response?.data ?? response;
}

async function refreshToken(refreshTokenValue) {
  return apiFetch("/auth/oauth/token", {
    method: "POST",
    body: {
      grant_type: "refresh_token",
      client_id: "1",
      refresh_token: refreshTokenValue,
      scope: ""
    }
  });
}

function extractStoredAuth(localStorageDump) {
  for (const value of Object.values(localStorageDump)) {
    if (typeof value !== "string") {
      continue;
    }
    try {
      const parsed = JSON.parse(value);
      if (parsed?.token?.access_token) {
        return parsed;
      }
      if (parsed?.access_token && parsed?.refresh_token) {
        return { token: parsed };
      }
    } catch {
      continue;
    }
  }
  return null;
}

async function dumpLocalStorage(page) {
  return page.evaluate(() => {
    const result = {};
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key) {
        result[key] = localStorage.getItem(key);
      }
    }
    return result;
  });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readStoredAuthFromContext(context) {
  for (const page of [...context.pages()].reverse()) {
    try {
      if (!page.url() || !page.url().startsWith(SITE_BASE)) {
        continue;
      }

      const storedAuth = extractStoredAuth(await dumpLocalStorage(page));
      if (storedAuth?.token?.access_token) {
        return storedAuth;
      }
    } catch (error) {
      const message = String(error?.message ?? error);
      if (
        message.includes("Execution context was destroyed") ||
        message.includes("Target page, context or browser has been closed")
      ) {
        continue;
      }
      throw error;
    }
  }

  return null;
}

async function waitForStoredAuth(context, { timeoutMs = 10 * 60_000, pollMs = 2_000 } = {}) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const storedAuth = await readStoredAuthFromContext(context);
    if (storedAuth?.token?.access_token) {
      return storedAuth;
    }

    await delay(pollMs);
  }

  throw new Error("Timed out waiting for RanobeLib login in the browser window.");
}

async function acquireAuthFromBrowser({ slug, profileDir, headlessLogin }) {
  await mkdir(profileDir, { recursive: true });

  let context;
  try {
    context = await chromium.launchPersistentContext(profileDir, {
      channel: "chrome",
      headless: headlessLogin,
      viewport: { width: 1440, height: 960 }
    });
  } catch (error) {
    throw new Error(
      `Cannot launch Chrome profile for RanobeLib auth: ${error.message}\nTry: npx playwright install chrome`
    );
  }

  try {
    const page = context.pages()[0] ?? (await context.newPage());
    await page.goto(`${SITE_BASE}/ru/book/${slug}?section=chapters`, {
      waitUntil: "domcontentloaded"
    });
    await page.waitForTimeout(1500);

    let storedAuth = extractStoredAuth(await dumpLocalStorage(page));
    if (!storedAuth?.token?.access_token) {
      if (headlessLogin) {
        throw new Error("No saved RanobeLib session in the browser profile.");
      }
      console.log(
        "Open browser ready. Log into RanobeLib in that window. The script will continue automatically once auth appears."
      );
      storedAuth = await waitForStoredAuth(context);
    }

    if (!storedAuth?.token?.access_token) {
      throw new Error("RanobeLib auth token was not found in localStorage after login.");
    }

    return storedAuth;
  } finally {
    await context.close();
  }
}

async function resolveAuth({ slug, profileDir, headlessLogin }) {
  const storedAuth = await acquireAuthFromBrowser({ slug, profileDir, headlessLogin });
  let token = storedAuth.token;

  if (token?.refresh_token) {
    const expiresAt = (token.timestamp ?? Date.now()) + Number(token.expires_in ?? 0) * 1000;
    if (!token.access_token || expiresAt <= Date.now()) {
      token = await refreshToken(token.refresh_token);
    }
  }

  if (!token?.access_token) {
    throw new Error("No valid access token available for RanobeLib.");
  }

  const auth = await fetchAuthMe(token.access_token);
  return { token, auth };
}

function getLatestChapterConfig(chapters, requestedVolume) {
  const sameVolume = chapters
    .filter((chapter) => String(chapter.volume) === String(requestedVolume))
    .sort((left, right) => Number(left.number) - Number(right.number));

  const latest = sameVolume.at(-1) ?? chapters.at(-1) ?? null;
  if (!latest) {
    return { latest: null, teamIds: [], branchId: null };
  }

  const branchSource = latest.branches?.[0] ?? latest;
  const teamIds = (branchSource.teams ?? []).map((team) => Number(team.id)).filter(Number.isFinite);
  const branchId = branchSource.branch_id ?? latest.branch_id ?? null;
  return { latest, teamIds, branchId };
}

function chapterHasBranch(chapter, branchId) {
  if (branchId === undefined || branchId === null) {
    return true;
  }

  if (Number(chapter.branch_id) === Number(branchId)) {
    return true;
  }

  return (chapter.branches ?? []).some(
    (branch) => Number(branch.branch_id ?? branch.id) === Number(branchId)
  );
}

function chapterHasTeam(chapter, teamId) {
  if (teamId === undefined || teamId === null) {
    return true;
  }

  const branchTeams = (chapter.branches ?? []).flatMap((branch) => branch.teams ?? []);
  const directTeams = chapter.teams ?? [];
  return [...branchTeams, ...directTeams].some((team) => Number(team.id) === Number(teamId));
}

function filterExistingChapters(chapters, { branchId, teamId, volume }) {
  return chapters.filter((chapter) => {
    if (volume !== undefined && String(chapter.volume) !== String(volume)) {
      return false;
    }
    return chapterHasBranch(chapter, branchId) && chapterHasTeam(chapter, teamId);
  });
}

function stripChapterHeading(rawText, chapterNumber) {
  const lines = rawText.replace(/\r\n/g, "\n").split("\n");
  while (lines.length && !lines[0].trim()) {
    lines.shift();
  }

  const headingPattern = new RegExp(`^Глава\\s+${chapterNumber}(?:\\D.*)?$`, "i");
  if (lines.length && headingPattern.test(lines[0].trim())) {
    lines.shift();
  }

  while (
    lines.length &&
    (!lines[0].trim() || /^\d{1,4}$/.test(lines[0].trim()))
  ) {
    lines.shift();
  }

  return lines.join("\n").trim();
}

function textToDocContent(text) {
  const blocks = text
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean);

  return {
    type: "doc",
    content: blocks.map((block) => ({
      type: "paragraph",
      content: [{ type: "text", text: block.replace(/\n+/g, " ").trim() }]
    }))
  };
}

function isDuplicateChapterError(error) {
  const message = String(error?.message ?? error);
  return message.includes("POST /chapters failed: 422") && message.includes("уже существует");
}

async function readChapterPayload({
  chapterNumber,
  sourceDir,
  mangaId,
  volume,
  teamIds,
  branchId,
  title,
  publishAt
}) {
  const filePath = path.join(sourceDir, `${chapterNumber}.txt`);
  const raw = await readFile(filePath, "utf8");
  const bodyText = stripChapterHeading(raw, chapterNumber);
  if (!bodyText) {
    throw new Error(`Chapter ${chapterNumber} is empty after stripping heading: ${filePath}`);
  }

  return {
    volume: String(volume),
    number: String(chapterNumber),
    name: title ?? "",
    branch_id: branchId ?? null,
    content: textToDocContent(bodyText),
    manga_id: mangaId,
    teams: teamIds,
    pages: [],
    publish_at: publishAt ?? null,
    expired_type: 0,
    bundle_id: null,
    attachments: []
  };
}

async function loadChapterTitles(filePath) {
  const text = await readFile(filePath, "utf8");
  const map = new Map();
  const patterns = [
    /^Глава\s+(\d+)\s+[—-]\s+(.+)$/gm,
    /^Том\s+\d+\s+Глава\s+(\d+)\s+[—-]\s+(.+)$/gm
  ];

  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      map.set(Number.parseInt(match[1], 10), match[2].trim());
    }
  }

  return map;
}

async function appendLog(outputDir, entry) {
  await mkdir(outputDir, { recursive: true });
  const line = `${JSON.stringify(entry)}\n`;
  await appendFile(path.join(outputDir, "upload-log.jsonl"), line, "utf8");
}

async function main() {
  const options = parseCli();
  const mangaId = parseSlugId(options.slug);
  const existingChapters = await fetchExistingChapters(options.slug);
  const existingForTarget = filterExistingChapters(existingChapters, {
    branchId: options.branchId,
    teamId: options.teamId,
    volume: options.volume
  });
  const existingKeys = new Set(
    existingForTarget.map((chapter) => `${chapter.volume}:${chapter.number}`)
  );
  const latestSource = existingForTarget.length ? existingForTarget : existingChapters;
  const { latest, teamIds: latestTeamIds, branchId: latestBranchId } =
    getLatestChapterConfig(latestSource, options.volume);

  const fromChapter =
    options.from ??
    (() => {
      const sameVolumeNumbers = existingForTarget
        .filter((chapter) => String(chapter.volume) === options.volume)
        .map((chapter) => Number.parseInt(chapter.number, 10))
        .filter(Number.isFinite);
      return (Math.max(0, ...sameVolumeNumbers) || 0) + 1;
    })();

  const toChapter = options.to ?? 353;
  if (fromChapter > toChapter) {
    throw new Error(`Invalid chapter range: from ${fromChapter} is greater than to ${toChapter}`);
  }

  const teamIds = options.teamId ? [options.teamId] : latestTeamIds;
  if (!teamIds.length) {
    throw new Error(
      "Cannot determine upload team id automatically. Pass --team-id explicitly."
    );
  }

  const branchId =
    options.branchId === undefined ? latestBranchId ?? null : options.branchId;

  const titles = options.useTitles ? await loadChapterTitles(options.titlesFile) : null;
  const publishStartDate = options.publishStart ? parseDateInput(options.publishStart) : null;

  const chaptersToCreate = [];
  const missingTitles = [];

  for (let chapterNumber = fromChapter; chapterNumber <= toChapter; chapterNumber += 1) {
    const existingKey = `${options.volume}:${chapterNumber}`;
    if (existingKeys.has(existingKey)) {
      continue;
    }

    const publishAt =
      publishStartDate === null
        ? null
        : formatLocalDate(
            addMinutes(
              publishStartDate,
              options.publishIntervalMinutes * chaptersToCreate.length
            )
          );

    const title = titles ? titles.get(chapterNumber) ?? "" : "";
    if (titles && !title) {
      missingTitles.push(chapterNumber);
    }

    const payload = await readChapterPayload({
      chapterNumber,
      sourceDir: options.sourceDir,
      mangaId,
      volume: options.volume,
      teamIds,
      branchId,
      title,
      publishAt
    });

    chaptersToCreate.push({ chapterNumber, payload });
  }

  console.log(
    `RanobeLib: existing-total=${existingChapters.length}, existing-target=${existingForTarget.length}, latest=${latest ? `${latest.volume}:${latest.number}` : "none"}, queued=${chaptersToCreate.length}`
  );

  if (missingTitles.length) {
    console.warn(
      `Titles missing for chapters: ${missingTitles.join(", ")}. Empty names will be used for them.`
    );
  }

  if (!chaptersToCreate.length) {
    console.log("No missing chapters in the requested range.");
    return;
  }

  console.log(
    `Plan: ${chaptersToCreate[0].chapterNumber} -> ${chaptersToCreate.at(-1).chapterNumber}, volume ${options.volume}, teams=${teamIds.join(",")}, branch=${branchId ?? "null"}`
  );

  if (options.dryRun) {
    const preview = chaptersToCreate.slice(0, 2).map(({ chapterNumber, payload }) => ({
      chapterNumber,
      name: payload.name,
      publish_at: payload.publish_at,
      paragraphs: payload.content.content.length
    }));
    console.log("Dry-run preview:");
    console.log(JSON.stringify(preview, null, 2));
    return;
  }

  const { token, auth } = await resolveAuth({
    slug: options.slug,
    profileDir: options.profileDir,
    headlessLogin: options.headlessLogin
  });

  console.log(
    `Authenticated as ${auth?.username ?? auth?.id ?? "unknown"}`
  );

  const authTeamIds = (auth?.teams ?? []).map((team) => Number(team.id));
  const missingTeamPermission = teamIds.some((teamId) => !authTeamIds.includes(teamId));
  if (missingTeamPermission) {
    throw new Error(
      `Logged-in user ${auth?.username ?? auth?.id ?? "unknown"} is not a member of required team(s): ${teamIds.join(", ")}`
    );
  }

  for (const { chapterNumber, payload } of chaptersToCreate) {
    console.log(`Uploading chapter ${chapterNumber}...`);
    try {
      const response = await apiFetch("/chapters", {
        method: "POST",
        token: token.access_token,
        body: payload
      });
      const created = response?.data ?? response;
      await appendLog(options.outputDir, {
        createdAt: new Date().toISOString(),
        chapterNumber,
        payload: {
          volume: payload.volume,
          number: payload.number,
          name: payload.name,
          publish_at: payload.publish_at
        },
        response: {
          id: created?.id ?? null,
          volume: created?.volume ?? null,
          number: created?.number ?? null,
          name: created?.name ?? null
        }
      });
      console.log(
        `Created chapter ${chapterNumber} with id=${created?.id ?? "unknown"}`
      );
    } catch (error) {
      if (isDuplicateChapterError(error)) {
        console.warn(`Chapter ${chapterNumber} already exists, skipping.`);
        continue;
      }
      throw error;
    }
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
