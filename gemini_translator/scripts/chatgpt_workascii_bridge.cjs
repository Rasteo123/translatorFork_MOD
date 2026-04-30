const fs = require("fs/promises");
const path = require("path");
const readline = require("readline");
const { createRequire } = require("module");

const CHATGPT_URL = "https://chatgpt.com/";
const ORIGINS = ["https://chatgpt.com", "https://chat.openai.com", "https://auth.openai.com"];
const CHATGPT_APP_URL_PATTERN = /chatgpt\.com|chat\.openai\.com/i;
const CHATGPT_LOGIN_URL_PATTERN = /auth\.openai\.com|\/auth\//i;
const CHATGPT_EDITOR_SELECTOR =
  "textarea#prompt-textarea, textarea[data-testid='prompt-textarea'], #prompt-textarea[contenteditable='true'], [data-testid='prompt-textarea'], div#prompt-textarea[role='textbox'], div[contenteditable='true'][role='textbox']";
const CHATGPT_NEW_CHAT_PATTERN = /\u041d\u043e\u0432\u044b\u0439 \u0447\u0430\u0442|New chat/i;
const CHATGPT_LOGIN_BUTTON_PATTERN = /^(?:\u0412\u043e\u0439\u0442\u0438|Log in)$/i;
const CHATGPT_CONTINUE_WITH_GOOGLE_PATTERN =
  /Continue with Google|\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u044c (?:\u0441|\u0447\u0435\u0440\u0435\u0437) Google|Sign in with Google/i;
const CHATGPT_CONTINUE_GENERATION_PATTERN =
  /Continue generating|Continue writing|Continue response|\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u044c \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0435|\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u044c \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044e|\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u044c \u043e\u0442\u0432\u0435\u0442/i;
const SHARED_SUBMIT_COOLDOWN_MS = 3000;
const MANUAL_CHALLENGE_NOTICE_MS = 15000;
const PROMPT_MARKERS = [
  "[[CODEX_TRANSLATE_REQUEST]]",
  "[[CODEX_EDIT_REQUEST]]",
  "[[GLOSSARY_START]]",
  "[[GLOSSARY_END]]",
  "[[CHAPTER_START]]",
  "[[CHAPTER_END]]",
  "[[DRAFT_START]]",
  "[[DRAFT_END]]"
];

const state = {
  chromium: null,
  context: null,
  config: null,
  availablePages: [],
  pageWaiters: [],
  createdPages: 0,
  maxPages: 1,
  submitState: {
    tail: Promise.resolve(),
    nextAllowedAt: 0
  }
};

function respond(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function ok(id, extra = {}) {
  respond({ id: id || null, ok: true, ...extra });
}

function fail(id, code, error) {
  respond({ id: id || null, ok: false, code, error: String(error || "Unknown error") });
}

async function rmQuiet(targetPath) {
  await fs.rm(targetPath, { force: true, recursive: true }).catch(() => {});
}

async function cleanupProfileLocks(profileDir) {
  const transient = [
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
    "lockfile",
    path.join("Default", "LockFile")
  ];
  await Promise.all(transient.map((relativePath) => rmQuiet(path.join(profileDir, relativePath))));
}

async function grantClipboardPermissions(context) {
  for (const origin of ORIGINS) {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin }).catch(() => {});
  }
}

async function launchContext(chromium, profileDir, headless) {
  await fs.mkdir(profileDir, { recursive: true });
  await cleanupProfileLocks(profileDir);

  const launchOptions = {
    headless,
    locale: "ru-RU",
    viewport: { width: 1540, height: 1100 },
    args: ["--lang=ru-RU", "--disable-blink-features=AutomationControlled", "--no-sandbox"]
  };

  try {
    const context = await chromium.launchPersistentContext(profileDir, {
      ...launchOptions,
      channel: "chrome"
    });
    await grantClipboardPermissions(context);
    return context;
  } catch {
    const context = await chromium.launchPersistentContext(profileDir, launchOptions);
    await grantClipboardPermissions(context);
    return context;
  }
}

function pageIsClosed(page) {
  if (!page) {
    return true;
  }
  if (typeof page.isClosed !== "function") {
    return false;
  }
  try {
    return Boolean(page.isClosed());
  } catch {
    return true;
  }
}

function resetPoolState() {
  state.availablePages = [];
  state.pageWaiters = [];
  state.createdPages = 0;
  state.maxPages = 1;
  state.submitState = {
    tail: Promise.resolve(),
    nextAllowedAt: 0
  };
}

function initializePoolState(primaryPage, maxPages) {
  resetPoolState();
  state.maxPages = Math.max(1, Number(maxPages || 1));
  if (primaryPage) {
    state.availablePages.push(primaryPage);
    state.createdPages = 1;
  }
}

function rejectPageWaiters(error) {
  const waiters = state.pageWaiters.splice(0);
  for (const waiter of waiters) {
    waiter.reject(error);
  }
}

function refillPageWaiters() {
  while (state.pageWaiters.length > 0) {
    while (state.availablePages.length > 0) {
      const page = state.availablePages.pop();
      if (pageIsClosed(page)) {
        state.createdPages = Math.max(0, state.createdPages - 1);
        continue;
      }
      const waiter = state.pageWaiters.shift();
      waiter.resolve(page);
    }

    if (state.pageWaiters.length === 0 || !state.context || state.createdPages >= state.maxPages) {
      return;
    }

    const waiter = state.pageWaiters.shift();
    state.createdPages += 1;
    state.context
      .newPage()
      .then((page) => waiter.resolve(page))
      .catch((error) => {
        state.createdPages = Math.max(0, state.createdPages - 1);
        waiter.reject(error);
        refillPageWaiters();
      });
  }
}

async function acquirePageSlot() {
  if (!state.context || !state.config) {
    throw codeError("init_failed", "Bridge is not initialized.");
  }

  while (state.availablePages.length > 0) {
    const page = state.availablePages.pop();
    if (!pageIsClosed(page)) {
      return page;
    }
    state.createdPages = Math.max(0, state.createdPages - 1);
  }

  if (state.createdPages < state.maxPages) {
    state.createdPages += 1;
    try {
      return await state.context.newPage();
    } catch (error) {
      state.createdPages = Math.max(0, state.createdPages - 1);
      throw error;
    }
  }

  return new Promise((resolve, reject) => {
    state.pageWaiters.push({ resolve, reject });
  });
}

function releasePageSlot(page) {
  if (!page) {
    refillPageWaiters();
    return;
  }

  if (pageIsClosed(page)) {
    state.createdPages = Math.max(0, state.createdPages - 1);
    refillPageWaiters();
    return;
  }

  if (state.pageWaiters.length > 0) {
    const waiter = state.pageWaiters.shift();
    waiter.resolve(page);
    return;
  }

  state.availablePages.push(page);
}

async function acquireSharedSubmitSlot() {
  const submitState = state.submitState;
  const previousTail = submitState.tail.catch(() => {});
  let releaseSlot = () => {};

  submitState.tail = new Promise((resolve) => {
    releaseSlot = resolve;
  });

  await previousTail;

  const waitMs = Math.max(0, submitState.nextAllowedAt - Date.now());
  if (waitMs > 0) {
    await new Promise((resolve) => setTimeout(resolve, waitMs));
  }

  submitState.nextAllowedAt = Date.now() + SHARED_SUBMIT_COOLDOWN_MS;
  return () => {
    releaseSlot();
  };
}

async function isVisible(locator) {
  const count = await locator.count().catch(() => 0);
  if (!count) {
    return false;
  }
  return locator.first().isVisible().catch(() => false);
}

async function clickLocatorReliably(locator) {
  await locator.scrollIntoViewIfNeeded().catch(() => {});

  try {
    await locator.click({ timeout: 1000 });
    return true;
  } catch {
    // Try stronger fallbacks below.
  }

  try {
    await locator.click({ timeout: 1000, force: true });
    return true;
  } catch {
    // Fall through to DOM click.
  }

  try {
    await locator.evaluate((element) => element.click());
    return true;
  } catch {
    return false;
  }
}

async function clickFirstVisible(locatorCandidates) {
  for (const locator of locatorCandidates) {
    try {
      if (await locator.count().catch(() => 0)) {
        const first = locator.first();
        if (await first.isVisible().catch(() => false)) {
          if (await clickLocatorReliably(first)) {
            return true;
          }
        }
      }
    } catch {
      // Try the next candidate.
    }
  }
  return false;
}

async function hasVisibleLocator(locatorCandidates) {
  for (const locator of locatorCandidates) {
    if (await isVisible(locator)) {
      return true;
    }
  }
  return false;
}

function editor(page) {
  return page.locator(CHATGPT_EDITOR_SELECTOR).first();
}

function sendButton(page) {
  return page
    .locator(
      [
        "button[aria-label*='Send message']",
        "button[aria-label*='Send prompt']",
        "button[aria-label*='\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435']",
        "button[data-testid='composer-submit-button']",
        "button[data-testid='send-button']",
        "form[data-type='unified-composer'] button[type='submit']",
        "form button[type='submit']",
        "button[aria-label*='Send']",
        "button[aria-label*='Submit']",
        "button[aria-label*='\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c']"
      ].join(", ")
    )
    .first();
}

function stopButton(page) {
  return page
    .locator("button[aria-label*='Stop'], button[aria-label*='\u041e\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c'], button[data-testid='stop-button']")
    .first();
}

function getLoginButtonCandidates(page) {
  return [
    page.locator("[data-testid='login-button']").first(),
    page.getByRole("button", { name: CHATGPT_LOGIN_BUTTON_PATTERN }).first(),
    page.getByRole("link", { name: CHATGPT_LOGIN_BUTTON_PATTERN }).first(),
    page.getByRole("button", { name: CHATGPT_CONTINUE_WITH_GOOGLE_PATTERN }).first(),
    page.getByRole("link", { name: CHATGPT_CONTINUE_WITH_GOOGLE_PATTERN }).first(),
    page.locator("a[href*='login']").first(),
    page.locator("button[data-testid*='login']").first(),
    page.locator("[data-provider='google']").first(),
    page.locator("[data-testid*='google']").first()
  ];
}

function uploadButton(page) {
  return page
    .locator(
      "button[data-testid='composer-plus-btn'], button[aria-label*='Add files'], button[aria-label*='Add photos'], button[aria-label*='\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0444\u0430\u0439\u043b']"
    )
    .first();
}

function uploadMenuCandidates(page) {
  const uploadLabel =
    /Upload files|Upload from computer|Add files|Attach files|\u0417\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u0444\u0430\u0439\u043b|\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0444\u0430\u0439\u043b|\u041f\u0440\u0438\u043a\u0440\u0435\u043f\u0438\u0442\u044c \u0444\u0430\u0439\u043b/i;
  return [
    page.locator("[data-testid='file-upload-button']").first(),
    page.getByRole("menuitem", { name: uploadLabel }).first(),
    page.getByRole("button", { name: uploadLabel }).first(),
    page.getByRole("option", { name: uploadLabel }).first(),
    page.locator("button, [role='menuitem'], [role='option']").filter({ hasText: uploadLabel }).first()
  ];
}

async function getSupportedLocalFileTrigger(page) {
  const fileInputs = page.locator("input[type='file']");
  const count = await fileInputs.count().catch(() => 0);
  for (let index = 0; index < count; index += 1) {
    const candidate = fileInputs.nth(index);
    const supported = await candidate
      .evaluate((input) => {
        const imageOnlyTokens = new Set([
          ".png",
          ".jpg",
          ".jpeg",
          ".gif",
          ".webp",
          ".bmp",
          ".svg",
          ".avif",
          ".heic",
          ".heif"
        ]);

        const accept = (input.getAttribute("accept") || "").trim().toLowerCase();
        if (!accept) {
          return true;
        }

        const tokens = accept
          .split(",")
          .map((token) => token.trim())
          .filter(Boolean);
        if (!tokens.length) {
          return true;
        }

        return tokens.some((token) => {
          if (
            token === "*/*" ||
            token === ".txt" ||
            token === ".md" ||
            token === ".markdown" ||
            token === ".json" ||
            token === ".csv" ||
            token === ".pdf" ||
            token === ".doc" ||
            token === ".docx" ||
            token === ".rtf" ||
            token.startsWith("text/") ||
            token.startsWith("application/")
          ) {
            return true;
          }
          if (token.startsWith("image/") || imageOnlyTokens.has(token)) {
            return false;
          }
          return true;
        });
      })
      .catch(() => false);
    if (supported) {
      return candidate;
    }
  }
  return null;
}

async function waitForSupportedLocalFileTrigger(page, timeoutMs = 15000) {
  const attempts = Math.max(1, Math.ceil(timeoutMs / 300));
  let clickedUploadButton = false;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const trigger = await getSupportedLocalFileTrigger(page);
    if (trigger) {
      return trigger;
    }

    if (!clickedUploadButton && (await isVisible(uploadButton(page)))) {
      await clickLocatorReliably(uploadButton(page)).catch(() => false);
      clickedUploadButton = true;
      await page.waitForTimeout(500);
      continue;
    }

    if (clickedUploadButton && (await clickFirstVisible(uploadMenuCandidates(page)))) {
      await page.waitForTimeout(500);
      continue;
    }

    await page.waitForTimeout(300);
  }
  return null;
}

async function uploadFailureMessage(page) {
  const bodyText = await page.locator("body").innerText().catch(() => "");
  if (!bodyText) {
    return "";
  }

  const failurePattern =
    /files\.oaiusercontent\.com|failed to upload|unable to upload|could not upload|upload failed|\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c|\u0441\u0431\u043e\u0439 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438/i;
  if (!failurePattern.test(bodyText)) {
    return "";
  }

  const lines = bodyText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const matchingLine = lines.find((line) => failurePattern.test(line));
  return (matchingLine || "ChatGPT reported a file upload failure.").slice(0, 500);
}

async function throwIfUploadFailed(page) {
  const message = await uploadFailureMessage(page);
  if (message) {
    throw new Error(`ChatGPT rejected file upload: ${message}`);
  }
}

async function composerHasAttachment(page, fileName) {
  return page
    .evaluate((expectedName) => {
      const editor =
        document.querySelector("#prompt-textarea, [data-testid='prompt-textarea']") ||
        document.querySelector("[contenteditable='true'][role='textbox']");
      const composer = editor?.closest("form") || document.querySelector("form[data-type='unified-composer']");
      if (!composer) {
        return false;
      }

      const normalizedName = String(expectedName || "").toLowerCase();
      if (!normalizedName) {
        return false;
      }

      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
      };

      const nodes = [composer, ...Array.from(composer.querySelectorAll("*"))];
      return nodes.some((node) => {
        if (node !== composer && !visible(node)) {
          return false;
        }
        const text = [
          node.getAttribute?.("aria-label") || "",
          node.getAttribute?.("title") || "",
          node.innerText || "",
          node.textContent || ""
        ]
          .join(" ")
          .toLowerCase();
        return text.includes(normalizedName);
      });
    }, fileName)
    .catch(() => false);
}

async function waitForAttachedFileChips(page, files, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  const fileNames = files.map((filePath) => path.basename(filePath));
  let visibleSince = null;
  let lastMissing = fileNames;

  while (Date.now() < deadline) {
    await throwIfUploadFailed(page);
    const missing = [];
    for (const fileName of fileNames) {
      if (!(await composerHasAttachment(page, fileName))) {
        missing.push(fileName);
      }
    }
    if (!missing.length) {
      visibleSince = visibleSince || Date.now();
      if (Date.now() - visibleSince >= 2000) {
        await throwIfUploadFailed(page);
        return;
      }
    } else {
      visibleSince = null;
      lastMissing = missing;
    }
    await page.waitForTimeout(500);
  }

  await throwIfUploadFailed(page);
  throw new Error(`ChatGPT did not show attached files: ${lastMissing.join(", ")}`);
}

async function composerAttachmentNames(page) {
  return page
    .evaluate(() => {
      const editor =
        document.querySelector("#prompt-textarea, [data-testid='prompt-textarea']") ||
        document.querySelector("[contenteditable='true'][role='textbox']");
      const composer = editor?.closest("form") || document.querySelector("form[data-type='unified-composer']");
      if (!composer) {
        return [];
      }
      const text = composer.innerText || composer.textContent || "";
      const matches = text.match(/[^\s\\/:*?"<>|]+?\.(?:txt|md|markdown|json|csv|pdf|docx?|rtf)\b/gi) || [];
      return Array.from(new Set(matches.map((value) => value.trim()).filter(Boolean)));
    })
    .catch(() => []);
}

async function clickComposerAttachmentRemove(page) {
  return page
    .evaluate(() => {
      const editor =
        document.querySelector("#prompt-textarea, [data-testid='prompt-textarea']") ||
        document.querySelector("[contenteditable='true'][role='textbox']");
      const composer = editor?.closest("form") || document.querySelector("form[data-type='unified-composer']");
      if (!composer) {
        return false;
      }

      const filePattern = /\.(?:txt|md|markdown|json|csv|pdf|docx?|rtf)\b/i;
      const removePattern = /remove|delete|clear|close|удал|убрать|закрыть/i;
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
      };

      const buttons = Array.from(composer.querySelectorAll("button, [role='button']"));
      for (const button of buttons) {
        if (!visible(button)) {
          continue;
        }
        const aria = button.getAttribute("aria-label") || "";
        const title = button.getAttribute("title") || "";
        const text = button.innerText || button.textContent || "";
        const label = `${aria} ${title} ${text}`;
        const container = button.closest("[role='group'], li, [data-testid*='attachment'], [class*='attachment'], div");
        const containerText = container ? container.innerText || container.textContent || "" : "";
        if (removePattern.test(label) && (filePattern.test(containerText) || /attachment|file|файл/i.test(label))) {
          button.click();
          return true;
        }
      }

      const fileNodes = Array.from(composer.querySelectorAll("*")).filter((node) => {
        const text = node.innerText || node.textContent || "";
        return filePattern.test(text) && visible(node);
      });
      for (const node of fileNodes) {
        const container = node.closest("[role='group'], li, [data-testid*='attachment'], [class*='attachment'], div") || node.parentElement;
        if (!container) {
          continue;
        }
        const removeButton = Array.from(container.querySelectorAll("button, [role='button']")).find((button) => {
          if (!visible(button)) {
            return false;
          }
          const aria = button.getAttribute("aria-label") || "";
          const title = button.getAttribute("title") || "";
          const text = button.innerText || button.textContent || "";
          return removePattern.test(`${aria} ${title} ${text}`);
        });
        if (removeButton) {
          removeButton.click();
          return true;
        }
      }

      return false;
    })
    .catch(() => false);
}

async function clearComposerAttachments(page) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const names = await composerAttachmentNames(page);
    if (!names.length) {
      return;
    }
    const clicked = await clickComposerAttachmentRemove(page);
    if (!clicked) {
      console.error(`[chatgpt_workascii_bridge] Could not remove existing composer attachment(s): ${names.join(", ")}`);
      return;
    }
    await page.waitForTimeout(400);
  }
}

async function hasOnlyExpectedComposerAttachments(page, files) {
  const names = await composerAttachmentNames(page);
  if (!names.length) {
    return true;
  }
  const expected = files.map((filePath) => path.basename(filePath));
  return names.length === expected.length && names.every((name) => expected.includes(name));
}

async function uploadFilesOnce(page, files) {
  const fileTrigger = await waitForSupportedLocalFileTrigger(page, 15000);
  if (fileTrigger) {
    await fileTrigger.setInputFiles(files);
    await waitForAttachedFileChips(page, files, 30000);
    return;
  }

  const fileChooserPromise = typeof page.waitForEvent === "function"
    ? page.waitForEvent("filechooser", { timeout: 8000 }).catch(() => null)
    : Promise.resolve(null);
  await clickLocatorReliably(uploadButton(page)).catch(() => false);
  await page.waitForTimeout(400);
  await clickFirstVisible(uploadMenuCandidates(page)).catch(() => false);
  const fileChooser = await fileChooserPromise;
  if (!fileChooser) {
    throw new Error("ChatGPT file input was not found.");
  }
  await fileChooser.setFiles(files);
  await waitForAttachedFileChips(page, files, 30000);
}

async function uploadFiles(page, files) {
  await waitForEditor(page, 30000);
  await clearComposerAttachments(page);
  await uploadFilesOnce(page, files);

  if (!(await hasOnlyExpectedComposerAttachments(page, files))) {
    const names = await composerAttachmentNames(page);
    console.error(
      `[chatgpt_workascii_bridge] Composer attachment set after upload differs from requested file; keeping it to avoid deleting the active upload. Current: ${names.join(", ")}`
    );
  }
}

function assistantTurns(page) {
  return page.locator("section[data-turn='assistant'], article[data-turn='assistant'], [data-testid^='conversation-turn'][data-turn='assistant']");
}

function conversationHistoryRateLimitModal(page) {
  return page.locator("[data-testid='modal-conversation-history-rate-limit']").first();
}

async function dismissConversationHistoryRateLimit(page) {
  const modal = conversationHistoryRateLimitModal(page);
  if (!(await isVisible(modal))) {
    return false;
  }

  const dismissed = await clickFirstVisible([
    modal.getByRole("button", { name: /^(?:\u041f\u043e\u043d\u044f\u0442\u043d\u043e|Got it)$/i }),
    page.getByRole("button", { name: /^(?:\u041f\u043e\u043d\u044f\u0442\u043d\u043e|Got it)$/i })
  ]);

  if (dismissed) {
    await page.waitForTimeout(300);
  }
  return dismissed;
}

async function dismissCommonUi(page) {
  const labels = [
    /^(?:\u0417\u0430\u043a\u0440\u044b\u0442\u044c|Close)$/i,
    /^(?:\u041f\u043e\u043d\u044f\u0442\u043d\u043e|Got it)$/i,
    /^(?:\u041d\u0435 \u0441\u0435\u0439\u0447\u0430\u0441|Not now)$/i,
    /^(?:\u0421\u043e\u0433\u043b\u0430\u0441\u0435\u043d|Agree)$/i
  ];

  let clickedAny = false;
  for (let pass = 0; pass < 3; pass += 1) {
    let clicked = false;
    for (const label of labels) {
      if (await clickFirstVisible([page.getByRole("button", { name: label }).first()])) {
        await page.waitForTimeout(300);
        clicked = true;
        clickedAny = true;
      }
    }
    if (!clicked) {
      break;
    }
  }
  return clickedAny;
}

async function dismissTransientChatGptUi(page) {
  let dismissedAny = false;
  for (let pass = 0; pass < 3; pass += 1) {
    const dismissedRateLimit = await dismissConversationHistoryRateLimit(page);
    const dismissedCommon = await dismissCommonUi(page);
    if (!(dismissedRateLimit || dismissedCommon)) {
      break;
    }
    dismissedAny = true;
    await page.waitForTimeout(250);
  }
  return dismissedAny;
}

function normalizeResponseText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function normalizeCandidateText(value) {
  return String(value || "")
    .replace(/\r/g, "")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function countCyrillicText(value) {
  return (String(value || "").match(/[\u0400-\u04FF]/g) || []).length;
}

function getPromptMarkers() {
  return PROMPT_MARKERS.slice();
}

function buildPromptEchoGuard(submittedPrompt = "") {
  const normalizedPrompt = normalizeResponseText(submittedPrompt);
  const promptMarkers = getPromptMarkers().filter((marker) => normalizedPrompt.includes(marker));
  const promptSnippets = String(submittedPrompt || "")
    .split(/\r?\n/)
    .map((line) => normalizeResponseText(line))
    .filter((line) => line.length >= 24)
    .slice(0, 8);
  return {
    normalizedPrompt,
    promptHead: normalizedPrompt.slice(0, 400),
    promptTail: normalizedPrompt.slice(-220),
    promptMarkers,
    promptSnippets
  };
}

function isPromptEchoCandidate(candidateText, promptEchoGuard = null) {
  const normalized = normalizeResponseText(candidateText);
  if (!normalized || !promptEchoGuard || !promptEchoGuard.normalizedPrompt) {
    return false;
  }
  if (normalized === promptEchoGuard.normalizedPrompt) {
    return true;
  }
  if (
    promptEchoGuard.promptHead &&
    normalized.startsWith(promptEchoGuard.promptHead) &&
    normalized.length >= Math.min(promptEchoGuard.normalizedPrompt.length, promptEchoGuard.promptHead.length + 80)
  ) {
    return true;
  }
  if (
    promptEchoGuard.promptTail &&
    promptEchoGuard.promptTail.length >= 80 &&
    normalized.includes(promptEchoGuard.promptTail)
  ) {
    return true;
  }
  if (Array.isArray(promptEchoGuard.promptMarkers) && promptEchoGuard.promptMarkers.some((marker) => normalized.includes(marker))) {
    return true;
  }
  if (Array.isArray(promptEchoGuard.promptSnippets)) {
    const matchedSnippets = promptEchoGuard.promptSnippets.filter((snippet) => snippet && normalized.includes(snippet));
    if (matchedSnippets.length >= 2) {
      return true;
    }
  }
  return false;
}

function looksLikeStructuredGlossary(text) {
  const normalized = normalizeCandidateText(text);
  if (normalized.length < 80) {
    return false;
  }

  if (/^```(?:json)?\s*[\[{]/i.test(normalized) || /^[\[{]/.test(normalized)) {
    const objectPairs = (normalized.match(/"[^"\n]+"\s*:\s*\{/g) || []).length;
    const rusFieldCount = (normalized.match(/"rus"\s*:/g) || []).length;
    if (objectPairs >= 1 || rusFieldCount >= 1) {
      return true;
    }
  }

  const headingCount = (normalized.match(/^#{1,6}\s+\S+/gm) || []).length;
  const tableRowCount = (normalized.match(/^\|.+\|$/gm) || []).length;
  const bulletCount = (normalized.match(/^(?:-|\*|\d+\.)\s+\S+/gm) || []).length;
  return headingCount >= 2 || tableRowCount >= 3 || bulletCount >= 5;
}

function looksLikeThinkingPreludeText(value) {
  const normalized = normalizeCandidateText(value);
  if (!normalized) {
    return false;
  }

  const flat = normalizeResponseText(normalized).toLowerCase();
  if (!flat) {
    return false;
  }

  const unwrapChatGptSaid = flat.replace(/^chatgpt\s+(?:said|сказал)\s*:?\s*/i, "");
  return /^(?:thinking|думаю|думaю|analyzing|анализирую|reasoning|рассуждаю|thought for|reasoned for)\b/i.test(
    unwrapChatGptSaid
  );
}

function isSubstantiveCandidateText(text, structured = false) {
  const normalized = normalizeCandidateText(text);
  if (!normalized) {
    return false;
  }
  if (looksLikeThinkingPreludeText(normalized)) {
    return false;
  }
  if (structured) {
    return true;
  }

  const cyrillic = countCyrillicText(normalized);
  const latin = (normalized.match(/[A-Za-z]/g) || []).length;
  const lineCount = normalized.split(/\n+/).map((line) => line.trim()).filter(Boolean).length;

  if (normalized.length < 20) {
    return false;
  }
  if (lineCount <= 2 && normalized.length < 60 && cyrillic + latin < 30) {
    return false;
  }
  return true;
}

function isAcceptableShortAssistantText(text) {
  const normalized = normalizeCandidateText(text);
  if (!normalized) {
    return false;
  }
  if (looksLikeThinkingPreludeText(normalized)) {
    return false;
  }
  if (/^(?:ChatGPT can make mistakes|You said|Ask ChatGPT|What are you working on)$/i.test(normalized)) {
    return false;
  }
  return true;
}

function normalizeCandidateObject(candidate, fallbackPriority = 0, options = {}) {
  if (!candidate) {
    return null;
  }

  if (typeof candidate === "string") {
    const text = normalizeCandidateText(candidate);
    if (!text) {
      return null;
    }
    const structured = looksLikeStructuredGlossary(text);
    if (!isSubstantiveCandidateText(text, structured) && !(options.allowShort && isAcceptableShortAssistantText(text))) {
      return null;
    }
    return {
      text,
      score: text.length,
      cyrillic: countCyrillicText(text),
      latin: (text.match(/[A-Za-z]/g) || []).length,
      priority: fallbackPriority,
      structured,
      allowShort: Boolean(options.allowShort)
    };
  }

  if (typeof candidate !== "object") {
    return null;
  }

  const text = normalizeCandidateText(candidate.text);
  if (!text) {
    return null;
  }
  const structured = typeof candidate.structured === "boolean" ? candidate.structured : looksLikeStructuredGlossary(text);
  const allowShort = Boolean(options.allowShort || candidate.allowShort);
  if (!isSubstantiveCandidateText(text, structured) && !(allowShort && isAcceptableShortAssistantText(text))) {
    return null;
  }

  return {
    text,
    score: Number.isFinite(candidate.score) ? Number(candidate.score) : text.length,
    cyrillic: Number.isFinite(candidate.cyrillic) ? Number(candidate.cyrillic) : countCyrillicText(text),
    latin: Number.isFinite(candidate.latin) ? Number(candidate.latin) : (text.match(/[A-Za-z]/g) || []).length,
    priority: Number.isFinite(candidate.priority) ? Number(candidate.priority) : fallbackPriority,
    structured,
    allowShort
  };
}

function chooseResponseCandidate(primaryCandidate, fallbackCandidate, promptEchoGuard = null) {
  const primary = normalizeCandidateObject(primaryCandidate);
  const fallback = normalizeCandidateObject(fallbackCandidate);
  const primaryUsable = Boolean(primary && !isPromptEchoCandidate(primary.text, promptEchoGuard));
  const fallbackUsable = Boolean(fallback && !isPromptEchoCandidate(fallback.text, promptEchoGuard));

  if (!primaryUsable) {
    return fallbackUsable ? fallback : null;
  }
  if (!fallbackUsable) {
    return primary;
  }

  if (fallback.structured && !primary.structured) {
    return fallback;
  }
  if (primary.structured && !fallback.structured) {
    return primary;
  }
  if (fallback.priority > primary.priority && fallback.text.length >= primary.text.length * 0.9) {
    return fallback;
  }
  if (fallback.priority === primary.priority && fallback.text.length > primary.text.length * 1.35) {
    return fallback;
  }
  if (
    fallback.priority === primary.priority &&
    fallback.score > primary.score * 1.4 &&
    fallback.text.length >= primary.text.length
  ) {
    return fallback;
  }
  return primary;
}

function newChatCandidates(page) {
  return [
    page.getByRole("link", { name: CHATGPT_NEW_CHAT_PATTERN }).first(),
    page.getByRole("button", { name: CHATGPT_NEW_CHAT_PATTERN }).first(),
    page.locator("[data-testid='create-new-chat-button']").first(),
    page.locator("a[data-testid='create-new-chat-button']").first()
  ];
}

function codeError(code, message) {
  const error = new Error(String(message || "Unknown error"));
  error.code = String(code || "runtime_error");
  return error;
}

async function waitForEditor(page, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await dismissTransientChatGptUi(page).catch(() => {});
    if (await isVisible(editor(page))) {
      return editor(page);
    }
    await page.waitForTimeout(300);
  }
  throw new Error("ChatGPT composer did not become ready.");
}

async function captureAssistantResponseSnapshot(page) {
  const turns = assistantTurns(page);
  const assistantTurnCount = await turns.count().catch(() => 0);
  let lastAssistantText = "";
  if (assistantTurnCount > 0) {
    lastAssistantText = await turns.last().innerText().catch(() => "");
  }
  return {
    assistantTurnCount,
    lastAssistantText: normalizeResponseText(lastAssistantText),
    lastAssistantRawText: normalizeCandidateText(lastAssistantText)
  };
}

function hasFreshAssistantState(currentState, responseGuard = null) {
  const turnCount = Math.max(0, Number(currentState && currentState.assistantTurnCount ? currentState.assistantTurnCount : 0));
  const baseline = Math.max(0, Number(responseGuard && responseGuard.assistantTurnCount ? responseGuard.assistantTurnCount : 0));
  if (turnCount > baseline) {
    return true;
  }

  if (turnCount === 0) {
    return false;
  }

  const currentText = normalizeResponseText(currentState && currentState.lastAssistantText);
  const baselineText = normalizeResponseText(responseGuard && responseGuard.lastAssistantText);
  return Boolean(currentText && currentText !== baselineText);
}

async function hasModelResponseStarted(page, responseGuard = null) {
  const currentState = await captureAssistantResponseSnapshot(page);
  return hasFreshAssistantState(currentState, responseGuard);
}

async function detectBlockedState(page) {
  const bodyText = await page.locator("body").innerText().catch(() => "");
  const html = await page.content().catch(() => "");
  if (/challenge-platform|turnstile|cf-chl-|cloudflare/i.test(html) || /cloudflare/i.test(bodyText)) {
    return {
      code: "cloudflare",
      message: "ChatGPT is behind a Cloudflare anti-bot challenge.",
      interactive: true
    };
  }
  if (/access denied|unsupported country|not available in your country/i.test(bodyText)) {
    return {
      code: "geoblock",
      message: "ChatGPT is blocked for the current location or session.",
      interactive: false
    };
  }
  return null;
}

function canWaitForInteractiveChallenge(config) {
  return Boolean(config && !config.headless);
}

function isCloudflareBlockedState(blockedState) {
  return Boolean(blockedState && String(blockedState.code || "").toLowerCase() === "cloudflare");
}

async function waitForManualChallengeClear(page, config, reason, timeoutMs = null) {
  const maxWaitMs = Math.max(
    60000,
    Number(timeoutMs || 0) || Math.max(60000, Number(config && config.timeout_sec ? config.timeout_sec : 1800) * 1000)
  );
  const deadline = Date.now() + maxWaitMs;
  const reasonMessage = String(reason && reason.message ? reason.message : "Cloudflare anti-bot challenge detected.");
  let lastNoticeAt = 0;

  await page.bringToFront().catch(() => {});

  while (Date.now() < deadline) {
    const blockedState = await detectBlockedState(page);
    const loginRequired = await isLoginPage(page).catch(() => false);

    if (!blockedState && !loginRequired) {
      return;
    }

    if (blockedState && (!blockedState.interactive || !isCloudflareBlockedState(blockedState))) {
      throw codeError(blockedState.code, blockedState.message);
    }

    const now = Date.now();
    if (now - lastNoticeAt >= MANUAL_CHALLENGE_NOTICE_MS) {
      console.error(
        `[chatgpt_workascii_bridge] Cloudflare challenge detected. Solve it in the opened browser window; the bridge will continue automatically. (${reasonMessage})`
      );
      lastNoticeAt = now;
    }

    await page.waitForTimeout(1000);
  }

  throw codeError("cloudflare", `${reasonMessage} Timed out while waiting for manual browser action.`);
}

async function isLoginPage(page) {
  if (CHATGPT_LOGIN_URL_PATTERN.test(page.url())) {
    return true;
  }

  if (await hasVisibleLocator(getLoginButtonCandidates(page))) {
    return true;
  }

  if (await isVisible(editor(page)) || (await hasVisibleLocator(newChatCandidates(page)))) {
    return false;
  }

  const bodyText = await page.locator("body").innerText().catch(() => "");
  return /(log in|sign up|continue with google|\u0432\u043e\u0439\u0442\u0438|\u0437\u0430\u0440\u0435\u0433\u0438\u0441\u0442\u0440|\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u044c (?:\u0441|\u0447\u0435\u0440\u0435\u0437) google)/i.test(bodyText);
}

async function maybeSelectWorkspace(page, workspaceIndex, workspaceName, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await isVisible(editor(page))) {
      return;
    }

    const bodyText = await page.locator("body").innerText().catch(() => "");
    if (!/workspace|organization|team|рабоч|команд|организац/i.test(bodyText) && !/auth\.openai\.com\/workspace/i.test(page.url())) {
      return;
    }

    const clicked = await page.evaluate(({ workspaceIndex: index, workspaceName: name }) => {
      const visible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        if (style.visibility === "hidden" || style.display === "none") return false;
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };

      const isBad = (text) =>
        /(create|new workspace|join|manage|settings|log ?out|switch account|back|cancel|close|создат|нов(ая|ое|ый)?\s+рабоч|присоед|настро|выйти|назад|отмена|закрыть)/i.test(text);

      const direct = Array.from(document.querySelectorAll("button[name='workspace_id']")).find((element) => visible(element));
      if (direct) {
        direct.click();
        return true;
      }

      const items = Array.from(document.querySelectorAll("button, a, [role='button']"))
        .filter((element) => visible(element))
        .map((element) => ({ element, text: (element.textContent || "").trim() }))
        .filter((entry) => entry.text && /(workspace|organization|team|рабоч|команд|организац)/i.test(entry.text))
        .filter((entry) => !isBad(entry.text));

      if (name) {
        const match = items.find((entry) => entry.text.toLowerCase().includes(String(name).trim().toLowerCase()));
        if (match) {
          match.element.click();
          return true;
        }
      }

      const indexed = items[Math.max(0, Number(index || 1) - 1)];
      if (indexed) {
        indexed.element.click();
        return true;
      }

      const fallback = items[0];
      if (fallback) {
        fallback.element.click();
        return true;
      }
      return false;
    }, { workspaceIndex, workspaceName });

    if (!clicked) {
      return;
    }

    await page.waitForTimeout(1400);
  }
}

async function openFreshChat(page, options = {}) {
  const config = state.config || {};
  const shouldNavigate =
    options.navigate === true || !CHATGPT_APP_URL_PATTERN.test(String(page.url ? page.url() : ""));

  if (shouldNavigate) {
    await page.goto(CHATGPT_URL, { waitUntil: "domcontentloaded", timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(1200);
  } else {
    await page.waitForTimeout(300);
  }

  const deadline = Date.now() + 45000;
  let usedNavigationFallback = false;

  while (Date.now() < deadline) {
    await dismissTransientChatGptUi(page).catch(() => {});

    const blockedState = await detectBlockedState(page);
    if (blockedState) {
      if (canWaitForInteractiveChallenge(config) && blockedState.interactive) {
        await waitForManualChallengeClear(page, config, blockedState);
        continue;
      }
      throw codeError(blockedState.code, blockedState.message);
    }

    if (await isLoginPage(page)) {
      const loginRequired = codeError("login_required", "ChatGPT login is required in the saved browser profile.");
      if (canWaitForInteractiveChallenge(config)) {
        await waitForManualReady(page, config, loginRequired);
        continue;
      }
      throw loginRequired;
    }

    await maybeSelectWorkspace(page, config.workspace_index || 1, config.workspace_name || "", 1500).catch(() => {});

    const composerVisible = await isVisible(editor(page));
    const assistantTurnCount = composerVisible ? await assistantTurns(page).count().catch(() => 0) : 0;
    const inConversation = /\/c\//i.test(String(page.url ? page.url() : ""));

    if (composerVisible && !inConversation) {
      await waitForEditor(page, 10000);
      return;
    }

    if (composerVisible && assistantTurnCount === 0 && inConversation) {
      const clickedNewChat = await clickFirstVisible(newChatCandidates(page));
      if (clickedNewChat) {
        await page.waitForTimeout(1200);
        continue;
      }
      await waitForEditor(page, 10000);
      return;
    }

    if (await clickFirstVisible(newChatCandidates(page))) {
      await page.waitForTimeout(1200);
      continue;
    }

    if (composerVisible && !inConversation) {
      await waitForEditor(page, 10000);
      return;
    }

    if (!usedNavigationFallback && CHATGPT_APP_URL_PATTERN.test(String(page.url ? page.url() : ""))) {
      usedNavigationFallback = true;
      await page.goto(CHATGPT_URL, { waitUntil: "domcontentloaded", timeout: 120000 }).catch(() => {});
      await page.waitForTimeout(1200);
      continue;
    }

    await page.waitForTimeout(500);
  }

  await waitForEditor(page, 30000);
}

async function waitForManualReady(page, config, reason) {
  const timeoutMs = Math.max(60000, Number(config.timeout_sec || 1800) * 1000);
  const deadline = Date.now() + timeoutMs;
  const reasonCode = String(reason && reason.code ? reason.code : "login_required");
  const reasonMessage = String(reason && reason.message ? reason.message : "Manual browser action is required.");

  console.error(`[chatgpt_workascii_bridge] Waiting for manual browser action: ${reasonMessage}`);
  await page.bringToFront().catch(() => {});

  while (Date.now() < deadline) {
    await page.waitForTimeout(1000);

    const blockedState = await detectBlockedState(page);
    if (blockedState && !blockedState.interactive) {
      throw codeError(blockedState.code, blockedState.message);
    }

    await maybeSelectWorkspace(page, config.workspace_index || 1, config.workspace_name || "", 1500).catch(() => {});

    if (await isVisible(editor(page))) {
      await waitForEditor(page, 30000);
      return;
    }

    if (!(await isLoginPage(page)) && !blockedState) {
      try {
        await openFreshChat(page);
        return;
      } catch {
        // Keep waiting while the user completes the login/challenge flow.
      }
    }
  }

  throw codeError(reasonCode, `${reasonMessage} Timed out while waiting for manual browser action.`);
}

async function ensureReady(page, config, options = {}) {
  const interactiveInit = Boolean(options.interactive_init && !config.headless);
  const interactiveRecovery = Boolean(!config.headless);
  const shouldNavigate =
    options.navigate !== false || !CHATGPT_APP_URL_PATTERN.test(String(page.url ? page.url() : ""));

  if (shouldNavigate) {
    await page.goto(CHATGPT_URL, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.waitForTimeout(1000);
  }

  const deadline = Date.now() + Math.min(Math.max(Number(config.timeout_sec || 1800) * 1000, 30000), 60000);
  while (Date.now() < deadline) {
    await dismissTransientChatGptUi(page).catch(() => {});

    const blockedState = await detectBlockedState(page);
    if (blockedState) {
      if ((interactiveInit || interactiveRecovery) && blockedState.interactive) {
        await waitForManualReady(page, config, blockedState);
        return;
      }
      throw codeError(blockedState.code, blockedState.message);
    }

    await maybeSelectWorkspace(
      page,
      config.workspace_index || 1,
      config.workspace_name || "",
      Math.min(Number(config.timeout_sec || 1800) * 1000, 20000)
    ).catch(() => {});

    if (await isVisible(editor(page))) {
      await waitForEditor(page, 30000);
      return;
    }

    if (await isLoginPage(page)) {
      const loginRequired = codeError("login_required", "ChatGPT login is required in the saved browser profile.");
      if (interactiveInit || interactiveRecovery) {
        await waitForManualReady(page, config, loginRequired);
        return;
      }
      throw loginRequired;
    }

    if (options.open_fresh_chat) {
      await openFreshChat(page, { navigate: false });
      return;
    }

    await page.waitForTimeout(500);
  }

  if (options.open_fresh_chat) {
    await openFreshChat(page, { navigate: false });
    return;
  }
  await waitForEditor(page, 30000);
}

async function readEditorText(input) {
  try {
    const text = await input.evaluate((element) => {
      if ("value" in element) {
        return String(element.value || "").trim();
      }
      return String(element.innerText || element.textContent || "").trim();
    });
    return normalizeCandidateText(text);
  } catch {
    return null;
  }
}

function hasFullPromptInEditor(editorText, promptText) {
  if (editorText === null) {
    return false;
  }
  const normalizedEditor = normalizeCandidateText(editorText).replace(/\s+/g, " ").trim();
  const normalizedPrompt = normalizeCandidateText(promptText).replace(/\s+/g, " ").trim();
  if (!normalizedEditor || !normalizedPrompt) {
    return false;
  }
  if (normalizedEditor === normalizedPrompt || normalizedEditor.includes(normalizedPrompt)) {
    return true;
  }

  const promptProbe = normalizedPrompt.slice(0, Math.min(120, normalizedPrompt.length));
  const promptTailProbe = normalizedPrompt.slice(-120);
  const minExpectedLength = Math.max(300, Math.floor(normalizedPrompt.length * 0.55));
  return (
    normalizedEditor.length >= minExpectedLength &&
    (!promptProbe || normalizedEditor.includes(promptProbe)) &&
    (!promptTailProbe || normalizedEditor.includes(promptTailProbe))
  );
}

async function setEditorTextDom(page, promptText) {
  return page
    .evaluate(
      ({ selector, text }) => {
        const element =
          document.querySelector(selector) ||
          document.querySelector("#prompt-textarea, [data-testid='prompt-textarea']") ||
          document.querySelector("[contenteditable='true'][role='textbox']");
        if (!element) {
          return false;
        }

        element.focus();
        if ("value" in element) {
          element.value = String(text || "");
          element.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, data: text, inputType: "insertText" }));
          element.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
          element.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        }

        element.innerHTML = "";
        const lines = String(text || "").split("\n");
        const doc = element.ownerDocument;
        for (const line of lines.length ? lines : [""]) {
          const paragraph = doc.createElement("p");
          if (line) {
            paragraph.textContent = line;
          } else {
            paragraph.innerHTML = "<br>";
          }
          element.appendChild(paragraph);
        }
        element.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, data: text, inputType: "insertText" }));
        element.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      },
      { selector: CHATGPT_EDITOR_SELECTOR, text: promptText }
    )
    .catch(() => false);
}

async function pastePromptFromClipboard(page, input, promptText) {
  const modifier = process.platform === "darwin" ? "Meta" : "Control";
  const wroteClipboard = await page
    .evaluate(async (text) => {
      await navigator.clipboard.writeText(String(text || ""));
      return true;
    }, promptText)
    .catch(() => false);
  if (!wroteClipboard) {
    return false;
  }
  await input.click({ timeout: 5000 }).catch(() => {});
  await page.keyboard.press(`${modifier}+A`).catch(() => {});
  await page.keyboard.press("Backspace").catch(() => {});
  await page.keyboard.press(`${modifier}+V`).catch(() => {});
  await page.waitForTimeout(400);
  return true;
}

async function fillPrompt(page, promptText) {
  const input = await waitForEditor(page, 30000);
  const modifier = process.platform === "darwin" ? "Meta" : "Control";

  await input.click({ timeout: 5000 }).catch(async () => {
    await input.evaluate((element) => element.focus()).catch(() => {});
  });
  await page.keyboard.press(`${modifier}+A`).catch(() => {});
  await page.keyboard.press("Backspace").catch(() => {});

  const filled = await input
    .fill(promptText)
    .then(() => true)
    .catch(async () => {
      await page.keyboard.insertText(promptText).catch(() => {});
      return false;
    });
  await page.waitForTimeout(250);

  let currentText = await readEditorText(input);
  if (hasFullPromptInEditor(currentText, promptText)) {
    return;
  }
  if (filled && currentText === null) {
    return;
  }

  const domFilled = await setEditorTextDom(page, promptText);
  await page.waitForTimeout(250);

  currentText = await readEditorText(input);
  if (hasFullPromptInEditor(currentText, promptText)) {
    return;
  }
  if (domFilled && currentText === null) {
    return;
  }

  const pasted = await pastePromptFromClipboard(page, input, promptText).catch(() => false);
  currentText = await readEditorText(input);
  if (hasFullPromptInEditor(currentText, promptText)) {
    return;
  }
  if (pasted && currentText === null) {
    return;
  }

  await input.click({ timeout: 5000 }).catch(() => {});
  await page.keyboard.press(`${modifier}+A`).catch(() => {});
  await page.keyboard.press("Backspace").catch(() => {});
  const typed = await page.keyboard.insertText(promptText).then(() => true).catch(() => false);
  await page.waitForTimeout(400);

  currentText = await readEditorText(input);
  if (!hasFullPromptInEditor(currentText, promptText) && !(typed && currentText === null)) {
    throw new Error("ChatGPT prompt text was not inserted into the editor.");
  }
}

async function waitForSendEnabled(page, timeoutMs = 30000) {
  let deadline = Date.now() + timeoutMs;
  const button = sendButton(page);
  while (Date.now() < deadline) {
    await dismissTransientChatGptUi(page).catch(() => {});
    const blockedState = await detectBlockedState(page);
    if (blockedState) {
      if (canWaitForInteractiveChallenge(state.config) && blockedState.interactive) {
        const startedAt = Date.now();
        await waitForManualChallengeClear(page, state.config, blockedState, timeoutMs);
        deadline += Date.now() - startedAt;
        continue;
      }
      throw codeError(blockedState.code, blockedState.message);
    }

    if (await isVisible(button)) {
      const disabled = await button.getAttribute("disabled").catch(() => null);
      const ariaDisabled = await button.getAttribute("aria-disabled").catch(() => null);
      const enabled = await button.isEnabled().catch(() => false);
      if (!disabled && ariaDisabled !== "true" && enabled) {
        return button;
      }
    }
    if (await hasSendButtonDom(page)) {
      return null;
    }
    await page.waitForTimeout(250);
  }
  throw new Error("ChatGPT send button did not become enabled.");
}

async function promptStillInComposer(page, submittedPrompt) {
  if (!submittedPrompt) {
    return false;
  }
  if (!(await isVisible(editor(page)))) {
    return false;
  }
  const currentText = await readEditorText(editor(page));
  if (currentText === null) {
    return true;
  }
  return hasFullPromptInEditor(currentText, submittedPrompt);
}

async function clickSendButtonDom(page) {
  return page
    .evaluate(() => {
      const editor =
        document.querySelector("#prompt-textarea, [data-testid='prompt-textarea']") ||
        document.querySelector("[contenteditable='true'][role='textbox']");
      const form = editor?.closest("form") || document.querySelector("form[data-type='unified-composer']");
      const root = form || document;
      const visible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
      };
      const enabled = (button) =>
        visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true";
      const labels = /send|submit|arrow|up|отправ/i;
      const nonSendLabels = /add|attach|upload|file|photo|image|plus|добав|прикреп|загруз|файл|изображ/i;
      const stopLabels = /stop|cancel|interrupt|abort|halt|pause|останов|прекрат|отмен/i;
      const candidates = Array.from(root.querySelectorAll("button")).filter((button) => {
        if (!enabled(button)) return false;
        const text = [
          button.getAttribute("data-testid") || "",
          button.getAttribute("aria-label") || "",
          button.getAttribute("title") || "",
          button.getAttribute("type") || "",
          button.innerText || "",
          button.textContent || ""
        ].join(" ");
        if (stopLabels.test(text)) return false;
        if (nonSendLabels.test(text)) return false;
        if (labels.test(text)) return true;
        if ((button.getAttribute("type") || "").toLowerCase() === "submit") return true;
        return Boolean(button.querySelector("svg"));
      });
      const button = candidates.find((candidate) => {
        const label = [
          candidate.getAttribute("data-testid") || "",
          candidate.getAttribute("aria-label") || "",
          candidate.getAttribute("title") || "",
          candidate.getAttribute("type") || ""
        ].join(" ");
        return /send|submit|отправ|composer-submit|send-button/i.test(label);
      }) || candidates.at(-1);

      if (button) {
        button.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, cancelable: true, view: window }));
        button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
        button.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, cancelable: true, view: window }));
        button.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
        button.click();
        return true;
      }
      if (form && typeof form.requestSubmit === "function") {
        form.requestSubmit();
        return true;
      }
      return false;
    })
    .catch(() => false);
}

async function hasSendButtonDom(page) {
  return page
    .evaluate(() => {
      const editor =
        document.querySelector("#prompt-textarea, [data-testid='prompt-textarea']") ||
        document.querySelector("[contenteditable='true'][role='textbox']");
      const form = editor?.closest("form") || document.querySelector("form[data-type='unified-composer']");
      const root = form || document;
      const visible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
      };
      return Array.from(root.querySelectorAll("button")).some((button) => {
        if (!visible(button) || button.disabled || button.getAttribute("aria-disabled") === "true") {
          return false;
        }
        const label = [
          button.getAttribute("data-testid") || "",
          button.getAttribute("aria-label") || "",
          button.getAttribute("title") || "",
          button.getAttribute("type") || ""
        ].join(" ");
        if (/stop|cancel|interrupt|abort|halt|pause|останов|прекрат|отмен/i.test(label)) {
          return false;
        }
        if (/add|attach|upload|file|photo|image|plus|добав|прикреп|загруз|файл|изображ/i.test(label)) {
          return false;
        }
        return /send|submit|отправ|composer-submit|send-button/i.test(label) || Boolean(button.querySelector("svg"));
      });
    })
    .catch(() => false);
}

async function submitPrompt(page, button, responseGuard = null, submittedPrompt = "") {
  const modifier = process.platform === "darwin" ? "Meta" : "Control";
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    if (attempt === 1) {
      if (button) {
        await clickLocatorReliably(button).catch(() => false);
      } else {
        await clickSendButtonDom(page).catch(() => false);
      }
    } else if (attempt === 2) {
      await clickSendButtonDom(page).catch(() => false);
    } else if (attempt === 3) {
      if (button) {
        await button.click({ timeout: 5000, force: true }).catch(() => {});
      } else {
        await clickSendButtonDom(page).catch(() => false);
      }
    } else if (attempt === 4) {
      await editor(page).click({ timeout: 5000 }).catch(() => {});
      await page.keyboard.press(`${modifier}+Enter`).catch(() => {});
    } else {
      await editor(page).click({ timeout: 5000 }).catch(() => {});
      await page.keyboard.press("Enter").catch(() => {});
    }
    const deadline = Date.now() + 6000;
    while (Date.now() < deadline) {
      const blockedState = await detectBlockedState(page);
      if (blockedState) {
        if (canWaitForInteractiveChallenge(state.config) && blockedState.interactive) {
          await waitForManualChallengeClear(page, state.config, blockedState);
          continue;
        }
        throw codeError(blockedState.code, blockedState.message);
      }

      if ((await isVisible(stopButton(page))) || (await hasModelResponseStarted(page, responseGuard))) {
        return;
      }

      const promptStillPresent = await promptStillInComposer(page, submittedPrompt).catch(() => true);
      const stillVisible = button ? await isVisible(button) : await hasSendButtonDom(page);
      const disabled = button && stillVisible
        ? Boolean((await button.getAttribute("disabled").catch(() => null)) || (await button.getAttribute("aria-disabled").catch(() => null)) === "true")
        : false;
      const enabled = button && stillVisible ? await button.isEnabled().catch(() => false) : stillVisible;

      if (!promptStillPresent && (!stillVisible || disabled || !enabled)) {
        return;
      }

      await page.waitForTimeout(1000);
    }
  }
  throw new Error("ChatGPT prompt was not sent.");
}

function continueGeneratingCandidates(page) {
  const buttonLocator = page.locator("button");
  const filteredButton =
    buttonLocator && typeof buttonLocator.filter === "function"
      ? buttonLocator.filter({ hasText: CHATGPT_CONTINUE_GENERATION_PATTERN }).first()
      : null;
  return [
    page.getByRole("button", { name: CHATGPT_CONTINUE_GENERATION_PATTERN }).first(),
    filteredButton,
    page
      .locator("[data-testid*='continue'], [aria-label*='Continue'], [aria-label*='\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u044c']")
      .first()
  ].filter(Boolean);
}

async function maybeClickContinue(page) {
  if (await clickFirstVisible(continueGeneratingCandidates(page))) {
    await page.waitForTimeout(1200);
  }
}

async function extractAssistantOnlyResponse(page, promptEchoGuard = null, responseGuard = null) {
  const promptMarkers = Array.isArray(promptEchoGuard && promptEchoGuard.promptMarkers)
    ? promptEchoGuard.promptMarkers
    : getPromptMarkers();
  const normalizedPrompt = String(promptEchoGuard && promptEchoGuard.normalizedPrompt ? promptEchoGuard.normalizedPrompt : "");
  const promptHead = String(promptEchoGuard && promptEchoGuard.promptHead ? promptEchoGuard.promptHead : "");
  const promptTail = String(promptEchoGuard && promptEchoGuard.promptTail ? promptEchoGuard.promptTail : "");
  const promptSnippets = Array.isArray(promptEchoGuard && promptEchoGuard.promptSnippets)
    ? promptEchoGuard.promptSnippets
    : [];
  const minAssistantTurnCount = Math.max(
    0,
    Number(responseGuard && responseGuard.assistantTurnCount ? responseGuard.assistantTurnCount : 0)
  );

  return page.evaluate(({ promptMarkers, normalizedPrompt, promptHead, promptTail, promptSnippets, minAssistantTurnCount }) => {
    const chapterHeadingPattern = /^\u0413\u043b\u0430\u0432\u0430\s+\d+/i;
    const assistantTurnSelector =
      "section[data-turn='assistant'], article[data-turn='assistant'], [data-testid^='conversation-turn'][data-turn='assistant']";

    function normalize(text) {
      return String(text || "")
        .replace(/\r/g, "")
        .replace(/\u00a0/g, " ")
        .replace(/[ \t]+\n/g, "\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
    }

    function normalizeFlat(text) {
      return normalize(text).replace(/\s+/g, " ");
    }

    function countCyrillic(text) {
      return (String(text || "").match(/[\u0400-\u04FF]/g) || []).length;
    }

    function looksLikeStructured(text) {
      const normalized = normalize(text);
      if (normalized.length < 80) {
        return false;
      }
      if (/^```(?:json)?\s*[\[{]/i.test(normalized) || /^[\[{]/.test(normalized)) {
        const objectPairs = (normalized.match(/"[^"\n]+"\s*:\s*\{/g) || []).length;
        const rusFieldCount = (normalized.match(/"rus"\s*:/g) || []).length;
        if (objectPairs >= 1 || rusFieldCount >= 1) {
          return true;
        }
      }
      return false;
    }

    function extractContentText(contentRoot) {
      if (!contentRoot) {
        return "";
      }

      const blocks = [];
      const blockTags = new Set(["P", "HR", "LI", "BLOCKQUOTE", "PRE", "H1", "H2", "H3", "H4", "H5", "H6"]);
      const skipTags = new Set(["DETAILS", "SUMMARY", "BUTTON"]);

      function collectBlocks(node) {
        for (const child of Array.from(node.children || [])) {
          if (skipTags.has(child.tagName)) {
            continue;
          }
          if (!blockTags.has(child.tagName)) {
            collectBlocks(child);
            continue;
          }
          if (child.tagName === "HR") {
            blocks.push("***");
            continue;
          }
          const text = (child.innerText || child.textContent || "").trim();
          if (text) {
            blocks.push(text);
          }
        }
      }

      collectBlocks(contentRoot);
      if (blocks.length > 0) {
        return blocks.join("\n\n");
      }

      const clone = contentRoot.cloneNode(true);
      for (const removable of clone.querySelectorAll("button, svg, use, audio")) {
        removable.remove();
      }
      for (const divider of clone.querySelectorAll("hr")) {
        divider.replaceWith(document.createTextNode("\n***\n"));
      }
      return clone.innerText || clone.textContent || "";
    }

    function looksLikeAttachmentHeader(text) {
      const lines = normalize(text)
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .slice(0, 8);
      const hasFileLikeName = lines.some((line) => /\.(?:md|txt|pdf|docx?|json)$/i.test(line));
      const hasAttachmentLabel = lines.some((line) => /^(?:File|Document|Attachment)$/i.test(line));
      return hasFileLikeName && hasAttachmentLabel;
    }

    function looksLikePromptEcho(text) {
      const normalized = normalizeFlat(text);
      if (!normalized) {
        return false;
      }
      if (normalizedPrompt && normalized === normalizedPrompt) {
        return true;
      }
      if (promptHead && normalized.startsWith(promptHead) && normalized.length >= Math.min(normalizedPrompt.length || normalized.length, promptHead.length + 80)) {
        return true;
      }
      if (promptTail && promptTail.length >= 80 && normalized.includes(promptTail)) {
        return true;
      }
      if (Array.isArray(promptMarkers) && promptMarkers.some((marker) => normalized.includes(marker))) {
        return true;
      }
      if (Array.isArray(promptSnippets)) {
        const matched = promptSnippets.filter((snippet) => snippet && normalized.includes(snippet));
        if (matched.length >= 2) {
          return true;
        }
      }
      return false;
    }

    function isBadCandidate(text) {
      if (!text) {
        return true;
      }
      if (/^(?:ChatGPT can make mistakes|You said|Ask ChatGPT|What are you working on)/i.test(text)) {
        return true;
      }
      if (looksLikeAttachmentHeader(text) || looksLikePromptEcho(text)) {
        return true;
      }
      return false;
    }

    function collectTurnSources(turn) {
      const assistantMessages = Array.from(turn.querySelectorAll("[data-message-author-role='assistant']")).filter(
        (node) => node.closest(assistantTurnSelector) === turn
      );
      return assistantMessages.length > 0 ? assistantMessages : [turn];
    }

    const assistantTurnNodes = Array.from(document.querySelectorAll(assistantTurnSelector));
    const minTurnIndex = Math.max(0, Number(minAssistantTurnCount) || 0);

    for (let turnIndex = assistantTurnNodes.length - 1; turnIndex >= minTurnIndex; turnIndex -= 1) {
      const turn = assistantTurnNodes[turnIndex];
      const candidates = [];
      const seen = new Set();
      let order = 0;

      function addCandidate(text, priority) {
        const normalized = normalize(text);
        const flat = normalizeFlat(normalized);
        const cyrillic = countCyrillic(normalized);
        const firstLine = normalized.split("\n").find((line) => line.trim()) || "";
        const hasHeading = chapterHeadingPattern.test(firstLine.trim());
        const structured = looksLikeStructured(normalized);
        if ((normalized.length < 40 && !structured) || (cyrillic < 5 && normalized.length < 80 && !hasHeading && !structured)) {
          return;
        }
        if (isBadCandidate(normalized)) {
          return;
        }
        const signature = flat.slice(0, 160);
        if (!signature || seen.has(signature)) {
          return;
        }
        seen.add(signature);
        candidates.push({
          text: normalized,
          cyrillic,
          priority,
          order: order++,
          structured
        });
      }

      for (const source of collectTurnSources(turn)) {
        const contentRoots = Array.from(source.querySelectorAll(".markdown, .prose, [class*='markdown']")).filter(
          (node) => !node.closest("details, summary")
        );
        if (contentRoots.length === 0) {
          addCandidate(extractContentText(source), 4);
          continue;
        }
        for (const contentRoot of contentRoots) {
          addCandidate(extractContentText(contentRoot), 4);
        }
        addCandidate(extractContentText(source), 3);
      }

      if (candidates.length === 0) {
        continue;
      }

      candidates.sort((a, b) => {
        if (Number(Boolean(b.structured)) !== Number(Boolean(a.structured))) {
          return Number(Boolean(b.structured)) - Number(Boolean(a.structured));
        }
        if (b.priority !== a.priority) {
          return b.priority - a.priority;
        }
        if (b.cyrillic !== a.cyrillic) {
          return b.cyrillic - a.cyrillic;
        }
        if (b.text.length !== a.text.length) {
          return b.text.length - a.text.length;
        }
        return b.order - a.order;
      });

      const best = candidates[0];
      return {
        text: best.text,
        score: best.cyrillic + best.text.length,
        cyrillic: best.cyrillic,
        latin: 0,
        priority: best.priority,
        structured: Boolean(best.structured)
      };
    }

    return null;
  }, { promptMarkers, normalizedPrompt, promptHead, promptTail, promptSnippets, minAssistantTurnCount });
}

async function extractBestResponse(page, promptEchoGuard = null, responseGuard = null) {
  const promptMarkers = Array.isArray(promptEchoGuard && promptEchoGuard.promptMarkers)
    ? promptEchoGuard.promptMarkers
    : getPromptMarkers();
  const normalizedPrompt = String(promptEchoGuard && promptEchoGuard.normalizedPrompt ? promptEchoGuard.normalizedPrompt : "");
  const promptHead = String(promptEchoGuard && promptEchoGuard.promptHead ? promptEchoGuard.promptHead : "");
  const promptTail = String(promptEchoGuard && promptEchoGuard.promptTail ? promptEchoGuard.promptTail : "");
  const promptSnippets = Array.isArray(promptEchoGuard && promptEchoGuard.promptSnippets)
    ? promptEchoGuard.promptSnippets
    : [];
  const minAssistantTurnCount = Math.max(
    0,
    Number(responseGuard && responseGuard.assistantTurnCount ? responseGuard.assistantTurnCount : 0)
  );

  return page.evaluate(({ promptMarkers, normalizedPrompt, promptHead, promptTail, promptSnippets, minAssistantTurnCount }) => {
    const assistantTurnSelector =
      "section[data-turn='assistant'], article[data-turn='assistant'], [data-testid^='conversation-turn'][data-turn='assistant']";

    function normalize(text) {
      return String(text || "")
        .replace(/\r/g, "")
        .replace(/\u00a0/g, " ")
        .replace(/[ \t]+\n/g, "\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
    }

    function normalizeFlat(text) {
      return normalize(text).replace(/\s+/g, " ");
    }

    function countCyrillic(text) {
      return (String(text || "").match(/[\u0400-\u04FF]/g) || []).length;
    }

    function looksLikeStructured(text) {
      const normalized = normalize(text);
      if (normalized.length < 80) {
        return false;
      }
      if (/^```(?:json)?\s*[\[{]/i.test(normalized) || /^[\[{]/.test(normalized)) {
        const objectPairs = (normalized.match(/"[^"\n]+"\s*:\s*\{/g) || []).length;
        const rusFieldCount = (normalized.match(/"rus"\s*:/g) || []).length;
        if (objectPairs >= 1 || rusFieldCount >= 1) {
          return true;
        }
      }
      return false;
    }

    function extractContentText(contentRoot) {
      if (!contentRoot) {
        return "";
      }
      const clone = contentRoot.cloneNode(true);
      for (const removable of clone.querySelectorAll("button, svg, use, audio")) {
        removable.remove();
      }
      for (const divider of clone.querySelectorAll("hr")) {
        divider.replaceWith(document.createTextNode("\n***\n"));
      }
      return clone.innerText || clone.textContent || "";
    }

    function looksLikeAttachmentHeader(text) {
      const lines = normalize(text)
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .slice(0, 8);
      const hasFileLikeName = lines.some((line) => /\.(?:md|txt|pdf|docx?|json)$/i.test(line));
      const hasAttachmentLabel = lines.some((line) => /^(?:File|Document|Attachment)$/i.test(line));
      return hasFileLikeName && hasAttachmentLabel;
    }

    function looksLikePromptEcho(text) {
      const normalized = normalizeFlat(text);
      if (!normalized) {
        return false;
      }
      if (normalizedPrompt && normalized === normalizedPrompt) {
        return true;
      }
      if (promptHead && normalized.startsWith(promptHead) && normalized.length >= Math.min(normalizedPrompt.length || normalized.length, promptHead.length + 80)) {
        return true;
      }
      if (promptTail && promptTail.length >= 80 && normalized.includes(promptTail)) {
        return true;
      }
      if (Array.isArray(promptMarkers) && promptMarkers.some((marker) => normalized.includes(marker))) {
        return true;
      }
      if (Array.isArray(promptSnippets)) {
        const matched = promptSnippets.filter((snippet) => snippet && normalized.includes(snippet));
        if (matched.length >= 2) {
          return true;
        }
      }
      return false;
    }

    function isBadCandidate(text) {
      if (!text) {
        return true;
      }
      if (/^(?:ChatGPT can make mistakes|You said|Ask ChatGPT|What are you working on)/i.test(text)) {
        return true;
      }
      if (looksLikeAttachmentHeader(text)) {
        return true;
      }
      return looksLikePromptEcho(text);
    }

    function collectTurnSources(turn) {
      const assistantMessages = Array.from(turn.querySelectorAll("[data-message-author-role='assistant']")).filter(
        (node) => node.closest(assistantTurnSelector) === turn
      );
      return assistantMessages.length > 0 ? assistantMessages : [turn];
    }

    const assistantTurnNodes = Array.from(document.querySelectorAll(assistantTurnSelector));
    const minTurnIndex = Math.max(0, Number(minAssistantTurnCount) || 0);

    for (let turnIndex = assistantTurnNodes.length - 1; turnIndex >= minTurnIndex; turnIndex -= 1) {
      const turn = assistantTurnNodes[turnIndex];
      const candidates = [];
      const seen = new Set();
      let order = 0;

      function addCandidate(text, priority) {
        const normalized = normalize(text);
        const flat = normalizeFlat(normalized);
        const structured = looksLikeStructured(normalized);
        const cyrillic = countCyrillic(normalized);
        if ((normalized.length < 60 && !structured) || (cyrillic < 3 && normalized.length < 100 && !structured)) {
          return;
        }
        if (isBadCandidate(normalized)) {
          return;
        }
        const signature = flat.slice(0, 160);
        if (!signature || seen.has(signature)) {
          return;
        }
        seen.add(signature);
        candidates.push({ text: normalized, priority, order: order++, cyrillic, structured });
      }

      for (const source of collectTurnSources(turn)) {
        const contentRoots = Array.from(source.querySelectorAll(".markdown, .prose, [class*='markdown']")).filter(
          (node) => !node.closest("details, summary")
        );
        if (contentRoots.length === 0) {
          addCandidate(extractContentText(source), 4);
          continue;
        }
        for (const contentRoot of contentRoots) {
          addCandidate(extractContentText(contentRoot), 4);
        }
        addCandidate(extractContentText(source), 3);
      }
      addCandidate(extractContentText(turn), 2);

      if (candidates.length === 0) {
        continue;
      }

      candidates.sort((a, b) => {
        if (Number(Boolean(b.structured)) !== Number(Boolean(a.structured))) {
          return Number(Boolean(b.structured)) - Number(Boolean(a.structured));
        }
        if (b.priority !== a.priority) {
          return b.priority - a.priority;
        }
        if (b.cyrillic !== a.cyrillic) {
          return b.cyrillic - a.cyrillic;
        }
        if (b.text.length !== a.text.length) {
          return b.text.length - a.text.length;
        }
        return b.order - a.order;
      });

      const best = candidates[0];
      return {
        text: best.text,
        score: best.cyrillic + best.text.length,
        cyrillic: best.cyrillic,
        latin: 0,
        priority: best.priority,
        structured: Boolean(best.structured)
      };
    }

    return null;
  }, { promptMarkers, normalizedPrompt, promptHead, promptTail, promptSnippets, minAssistantTurnCount });
}

async function clipboardText(page) {
  return page.evaluate(async () => {
    try {
      return String((await navigator.clipboard.readText()) || "");
    } catch {
      return "";
    }
  });
}

async function copyLatestAssistant(page, currentState = null) {
  await grantClipboardPermissions(page.context()).catch(() => {});
  const effectiveState = currentState || (await captureAssistantResponseSnapshot(page));
  if (!Math.max(0, Number(effectiveState && effectiveState.assistantTurnCount ? effectiveState.assistantTurnCount : 0))) {
    return null;
  }
  const turn = assistantTurns(page).last();

  await turn.scrollIntoViewIfNeeded().catch(() => {});
  await turn.hover({ force: true }).catch(() => {});

  const button = turn.locator("button[data-testid='copy-turn-action-button'], button[aria-label*='Скопировать'], button[aria-label*='Copy']").first();
  if ((await button.count().catch(() => 0)) === 0) {
    return null;
  }

  const before = normalizeCandidateText(await clipboardText(page));
  await button.click({ timeout: 5000 }).catch(() => {});

  for (let attempt = 0; attempt < 8; attempt += 1) {
    await page.waitForTimeout(250);
    const copied = normalizeCandidateText(await clipboardText(page));
    if (copied && copied !== before) {
      return copied;
    }
  }

  const copied = normalizeCandidateText(await clipboardText(page));
  return copied || null;
}

async function assistantFallback(page, responseGuard = null, currentState = null) {
  const effectiveState = currentState || (await captureAssistantResponseSnapshot(page).catch(() => null));
  if (!hasFreshAssistantState(effectiveState, responseGuard)) {
    return "";
  }
  if (effectiveState && effectiveState.lastAssistantRawText) {
    return normalizeCandidateText(effectiveState.lastAssistantRawText);
  }
  const turn = assistantTurns(page).last();
  return normalizeCandidateText(await turn.innerText().catch(() => ""));
}

async function waitForResponse(page, timeoutMs, responseGuard = null, submittedPrompt = "") {
  let deadline = Date.now() + timeoutMs;
  let bestText = "";
  let stableRounds = 0;
  let lastProgressAt = Date.now();
  let pollCount = 0;
  const promptEchoGuard = buildPromptEchoGuard(submittedPrompt);
  let echoedPromptSince = 0;
  let echoedPromptRounds = 0;
  const chapterHeadingPattern = /^\u0413\u043b\u0430\u0432\u0430\s+\d+/i;

  function looksLikeChapterTranslation(text) {
    const firstLine = String(text || "")
      .split("\n")
      .map((line) => line.trim())
      .find(Boolean);
    return chapterHeadingPattern.test(firstLine || "");
  }

  while (Date.now() < deadline) {
    await dismissTransientChatGptUi(page).catch(() => {});
    await maybeClickContinue(page).catch(() => {});
    pollCount += 1;

    const currentState = await captureAssistantResponseSnapshot(page).catch(() => ({
      assistantTurnCount: 0,
      lastAssistantText: "",
      lastAssistantRawText: ""
    }));
    const responseStarted = hasFreshAssistantState(currentState, responseGuard);

    const primaryCandidate = responseStarted
      ? normalizeCandidateObject(await extractAssistantOnlyResponse(page, promptEchoGuard, responseGuard).catch(() => null), 4)
      : null;
    const fallbackCandidate = responseStarted
      ? normalizeCandidateObject(await extractBestResponse(page, promptEchoGuard, responseGuard).catch(() => null), 2)
      : null;
    const domCandidate = chooseResponseCandidate(primaryCandidate, fallbackCandidate, promptEchoGuard);
    const snapshotCandidate = responseStarted
      ? normalizeCandidateObject(await assistantFallback(page, responseGuard, currentState).catch(() => ""), 1, { allowShort: true })
      : null;
    const shouldTryClipboard =
      responseStarted &&
      (!domCandidate || (!domCandidate.structured && domCandidate.text.length < 160) || pollCount % 4 === 0);
    const clipboardCandidate = shouldTryClipboard
      ? normalizeCandidateObject(await copyLatestAssistant(page, currentState).catch(() => null), 0, { allowShort: true })
      : null;
    const candidateObject = chooseResponseCandidate(
      chooseResponseCandidate(domCandidate, snapshotCandidate, promptEchoGuard),
      clipboardCandidate,
      promptEchoGuard
    );
    const candidate = candidateObject ? candidateObject.text : "";
    const echoedPrompt = !candidate && [primaryCandidate, fallbackCandidate, snapshotCandidate, clipboardCandidate]
      .filter(Boolean)
      .some((item) => isPromptEchoCandidate(item.text, promptEchoGuard));

    if (candidate) {
      if (candidate === bestText) {
        stableRounds += 1;
      } else {
        bestText = candidate;
        stableRounds = 0;
        lastProgressAt = Date.now();
      }
    }

    const blockedState = await detectBlockedState(page);
    if (blockedState) {
      if (canWaitForInteractiveChallenge(state.config) && blockedState.interactive) {
        const startedAt = Date.now();
        await waitForManualChallengeClear(page, state.config, blockedState, timeoutMs);
        deadline += Date.now() - startedAt;
        lastProgressAt = Date.now();
        continue;
      }
      throw codeError(blockedState.code, blockedState.message);
    }

    const bodyText = await page.locator("body").innerText().catch(() => "");
    if (/too many messages|rate limit|you've reached our limit|лимит/i.test(bodyText)) {
      const error = new Error("ChatGPT temporarily rate-limited the session.");
      error.code = "rate_limit";
      throw error;
    }

    const generating = await isVisible(stopButton(page));

    if (pollCount % 5 === 0) {
      console.error(
        `[chatgpt_workascii_bridge] poll=${pollCount} responseStarted=${responseStarted} generating=${generating} primaryLen=${primaryCandidate?.text?.length ?? 0} fallbackLen=${fallbackCandidate?.text?.length ?? 0} snapshotLen=${snapshotCandidate?.text?.length ?? 0} clipboardLen=${clipboardCandidate?.text?.length ?? 0} candidateLen=${candidate.length} bestLen=${bestText.length} stable=${stableRounds}`
      );
    }

    if (echoedPrompt) {
      if (!echoedPromptSince) {
        echoedPromptSince = Date.now();
      }
      echoedPromptRounds += 1;
      if (
        !generating &&
        echoedPromptRounds >= 6 &&
        Date.now() - echoedPromptSince >= 12000
      ) {
        throw codeError(
          "echoed_prompt",
          "ChatGPT echoed the submitted prompt instead of generating a reply."
        );
      }
    } else {
      echoedPromptSince = 0;
      echoedPromptRounds = 0;
    }

    if (bestText) {
      const structuredGlossary = looksLikeStructuredGlossary(bestText);
      if (structuredGlossary && bestText.length >= 80 && stableRounds >= 2 && !generating) {
        return bestText;
      }
      if (structuredGlossary && bestText.length >= 300 && stableRounds >= 6) {
        return bestText;
      }
      if (!generating && bestText.length >= 1000 && stableRounds >= 1) {
        return bestText;
      }
      if (!generating && stableRounds >= 2) {
        return bestText;
      }
      if (bestText.length >= 180 && stableRounds >= 2 && !generating) {
        return bestText;
      }
      if (looksLikeChapterTranslation(bestText) && stableRounds >= 2 && !generating) {
        return bestText;
      }
      if (Date.now() - lastProgressAt >= 20000 && stableRounds >= 4) {
        return bestText;
      }
      if (bestText.length >= 300 && stableRounds >= 6 && Date.now() - lastProgressAt >= 12000) {
        return bestText;
      }
      if (looksLikeChapterTranslation(bestText) && bestText.length >= 500 && stableRounds >= 8) {
        return bestText;
      }
    }

    await page.waitForTimeout(1500);
  }

  if (bestText) {
    return bestText;
  }

  const error = new Error(`Timed out after ${Math.round(timeoutMs / 1000)} seconds while waiting for ChatGPT response.`);
  error.code = "timeout";
  throw error;
}

function buildPrompt(prompt, systemInstruction) {
  const userPrompt = String(prompt || "");
  const systemText = String(systemInstruction || "").trim();
  return systemText ? `${systemText}\n\n${userPrompt}` : userPrompt;
}

function normalizeCode(error) {
  const explicit = String(error && error.code ? error.code : "").trim().toLowerCase();
  if (explicit) {
    return explicit;
  }
  const message = String(error && error.message ? error.message : error).toLowerCase();
  if (message.includes("cloudflare") || message.includes("blocked")) {
    return "blocked";
  }
  if (message.includes("login")) {
    return "login_required";
  }
  if (message.includes("rate")) {
    return "rate_limit";
  }
  if (message.includes("timeout")) {
    return "timeout";
  }
  return "runtime_error";
}

async function existingPath(targetPath) {
  if (!targetPath) {
    return null;
  }
  return fs.access(targetPath).then(() => targetPath).catch(() => null);
}

async function resolvePlaywrightPackageJson(config) {
  const explicitRoot = String(config.playwright_package_root || "").trim();
  const runtimeRoot = String(config.workascii_root || "").trim();

  const candidates = [
    explicitRoot ? path.join(explicitRoot, "package.json") : null,
    runtimeRoot ? path.join(runtimeRoot, "playwright_runtime", "package", "package.json") : null,
    runtimeRoot ? path.join(runtimeRoot, "node_modules", "playwright", "package.json") : null
  ];

  for (const candidate of candidates) {
    const resolved = await existingPath(candidate);
    if (resolved) {
      return resolved;
    }
  }
  return null;
}

async function initialize(config) {
  if (!config.profile_dir) {
    const error = new Error("profile_dir is required.");
    error.code = "config_error";
    throw error;
  }

  const packageJsonPath = await resolvePlaywrightPackageJson(config || {});
  if (!packageJsonPath) {
    const error = new Error("Playwright runtime package.json not found.");
    error.code = "config_error";
    throw error;
  }

  if (config.browsers_path) {
    process.env.PLAYWRIGHT_BROWSERS_PATH = String(config.browsers_path);
  }

  const workAsciiRequire = createRequire(packageJsonPath);
  const playwrightPackageRoot = path.dirname(packageJsonPath);
  const { chromium } = workAsciiRequire(playwrightPackageRoot);

  state.chromium = chromium;
  state.config = {
    workascii_root: String(config.workascii_root || "").trim(),
    playwright_package_root: String(config.playwright_package_root || "").trim(),
    browsers_path: String(config.browsers_path || "").trim(),
    workspace_name: String(config.workspace_name || "").trim(),
    workspace_index: Math.max(1, Number(config.workspace_index || 1)),
    headless: Boolean(config.headless),
    timeout_sec: Math.max(60, Number(config.timeout_sec || 1800)),
    parallel_requests: Math.max(1, Number(config.parallel_requests || 1)),
    profile_dir: String(config.profile_dir)
  };
  state.context = await launchContext(chromium, state.config.profile_dir, state.config.headless);
  const primaryPage = state.context.pages()[0] || (await state.context.newPage());
  await ensureReady(primaryPage, state.config, { interactive_init: true });
  initializePoolState(primaryPage, state.config.parallel_requests);
}

async function translate(prompt, systemInstruction) {
  if (!state.context || !state.config) {
    const error = new Error("Bridge is not initialized.");
    error.code = "init_failed";
    throw error;
  }

  const page = await acquirePageSlot();

  try {
    await page.bringToFront().catch(() => {});
    await openFreshChat(page);
    const preparedPrompt = buildPrompt(prompt, systemInstruction);
    await fillPrompt(page, preparedPrompt);
    const button = await waitForSendEnabled(page, 30000);
    const responseGuard = await captureAssistantResponseSnapshot(page);
    const releaseSubmitSlot = await acquireSharedSubmitSlot();
    try {
      await page.bringToFront().catch(() => {});
      await submitPrompt(page, button, responseGuard, preparedPrompt);
    } finally {
      releaseSubmitSlot();
    }
    return await waitForResponse(page, state.config.timeout_sec * 1000, responseGuard, preparedPrompt);
  } finally {
    releasePageSlot(page);
  }
}

async function shutdown() {
  rejectPageWaiters(codeError("init_failed", "Bridge is shutting down."));
  if (state.context) {
    await state.context.close().catch(() => {});
  }
  state.chromium = null;
  state.context = null;
  state.config = null;
  resetPoolState();
}

async function dispatch(message) {
  switch (message.type) {
    case "init":
      await initialize(message.config || {});
      return {};
    case "translate":
      return { text: await translate(message.prompt, message.system_instruction) };
    case "shutdown":
      await shutdown();
      return { shutdown: true };
    default:
      throw new Error(`Unsupported command: ${message.type}`);
  }
}

async function handleMessage(message) {
  try {
    const payload = await dispatch(message);
    ok(message.id, payload);
  } catch (error) {
    fail(message.id, normalizeCode(error), error.message || String(error));
  }
}

async function main() {
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  const pendingTasks = new Set();

  for await (const line of rl) {
    const trimmed = String(line || "").trim();
    if (!trimmed) {
      continue;
    }

    let message;
    try {
      message = JSON.parse(trimmed);
    } catch (error) {
      fail(null, "config_error", `Invalid JSON command: ${error.message}`);
      continue;
    }

    const task = handleMessage(message).finally(() => {
      pendingTasks.delete(task);
    });
    pendingTasks.add(task);

    if (message.type === "shutdown") {
      await task;
      await Promise.allSettled(Array.from(pendingTasks));
      return;
    }
  }

  await Promise.allSettled(Array.from(pendingTasks));
  await shutdown();
}

main()
  .then(() => {
    process.exit(0);
  })
  .catch(async (error) => {
    fail(null, normalizeCode(error), error.message || String(error));
    await shutdown().catch(() => {});
    process.exit(1);
  });
