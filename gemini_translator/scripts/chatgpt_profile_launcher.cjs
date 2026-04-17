const fs = require("fs/promises");
const path = require("path");
const { createRequire } = require("module");

const ORIGINS = ["https://chatgpt.com", "https://chat.openai.com", "https://auth.openai.com"];

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

async function launchContext(chromium, profileDir) {
  await fs.mkdir(profileDir, { recursive: true });
  await cleanupProfileLocks(profileDir);

  const launchOptions = {
    headless: false,
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

async function main() {
  const [, , profileDirArg, startUrlArg, packageRootArg, browsersPathArg] = process.argv;
  const profileDir = String(profileDirArg || "").trim();
  const startUrl = String(startUrlArg || "https://chatgpt.com/").trim();
  const packageRoot = String(packageRootArg || "").trim();
  const browsersPath = String(browsersPathArg || "").trim();

  if (!profileDir) {
    throw new Error("profile_dir argument is required.");
  }
  if (!packageRoot) {
    throw new Error("playwright package root argument is required.");
  }

  const packageJsonPath = path.join(packageRoot, "package.json");
  if (browsersPath) {
    process.env.PLAYWRIGHT_BROWSERS_PATH = browsersPath;
  }

  const workAsciiRequire = createRequire(packageJsonPath);
  const { chromium } = workAsciiRequire(packageRoot);
  const context = await launchContext(chromium, profileDir);

  try {
    const page = context.pages()[0] || (await context.newPage());
    await page.goto(startUrl, { waitUntil: "domcontentloaded", timeout: 120000 }).catch(() => {});
    await page.bringToFront().catch(() => {});
    await new Promise((resolve) => {
      context.on("close", resolve);
    });
  } finally {
    await context.close().catch(() => {});
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
