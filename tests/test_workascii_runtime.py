import asyncio
import json
import subprocess
import threading
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gemini_translator.api import config as api_config
from gemini_translator.api.base import get_worker_loop
from gemini_translator.api.handlers.workascii_chatgpt import WorkAsciiChatGptApiHandler


class WorkAsciiRuntimeConfigTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows-specific asyncio policy regression")
    def test_worker_loop_uses_subprocess_capable_loop_under_selector_policy(self):
        original_policy = asyncio.get_event_loop_policy()
        result = {}

        def worker():
            loop = None
            try:
                loop = get_worker_loop()
                result["loop_type"] = type(loop).__name__

                async def spawn_probe():
                    proc = await asyncio.create_subprocess_exec(
                        "cmd.exe",
                        "/c",
                        "echo ok",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await proc.communicate()
                    return proc.returncode, stdout.decode("utf-8", errors="ignore").strip(), stderr.decode("utf-8", errors="ignore").strip()

                result["probe"] = loop.run_until_complete(spawn_probe())
            except Exception as exc:
                result["exception"] = f"{type(exc).__name__}: {exc}"
            finally:
                if loop is not None:
                    loop.close()

        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
        finally:
            asyncio.set_event_loop_policy(original_policy)

        self.assertNotIn("exception", result, result.get("exception"))
        self.assertEqual(result.get("loop_type"), "ProactorEventLoop")
        self.assertEqual(result.get("probe"), (0, "ok", ""))

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific os_patch regression")
    def test_os_patch_preserves_asyncio_named_pipe_paths(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = """
import asyncio
import json
import sys
import traceback

import os_patch

async def main():
    os_patch.apply()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "print('ok')",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "rc": proc.returncode,
        "stdout": stdout.decode("utf-8", errors="ignore").strip(),
        "stderr": stderr.decode("utf-8", errors="ignore").strip(),
    }

payload = {}
try:
    payload = asyncio.run(main())
except Exception as exc:
    payload = {
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }

print(json.dumps(payload, ensure_ascii=False))
sys.exit(0 if "error" not in payload else 1)
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(completed.returncode, 0, payload.get("traceback") or completed.stderr)
        self.assertNotIn("error", payload, payload.get("traceback"))
        self.assertEqual(payload.get("rc"), 0)
        self.assertEqual(payload.get("stdout"), "ok")
        self.assertEqual(payload.get("stderr"), "")

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific memfs os.path regression")
    def test_os_patch_supports_exists_isdir_isfile_for_mem_paths(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = """
import json
import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

import os_patch

payload = {}
try:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    os_patch.apply()

    virtual_dir = os.path.join("mem://session-cache", "chapter-1")
    os.makedirs(virtual_dir)
    virtual_file = os.write_bytes_to_mem(b"hello", ".txt")
    payload = {
        "virtual_file": virtual_file,
        "file_exists": os.path.exists(virtual_file),
        "file_isfile": os.path.isfile(virtual_file),
        "file_isdir": os.path.isdir(virtual_file),
        "virtual_dir": virtual_dir,
        "dir_exists": os.path.exists(virtual_dir),
        "dir_isfile": os.path.isfile(virtual_dir),
        "dir_isdir": os.path.isdir(virtual_dir),
    }
except Exception as exc:
    payload = {
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }

print(json.dumps(payload, ensure_ascii=False))
sys.exit(0 if "error" not in payload else 1)
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        json_lines = [line for line in completed.stdout.strip().splitlines() if line.strip().startswith("{")]
        self.assertTrue(json_lines, completed.stdout)
        payload = json.loads(json_lines[-1])
        self.assertEqual(completed.returncode, 0, payload.get("traceback") or completed.stderr)
        self.assertNotIn("error", payload, payload.get("traceback"))
        self.assertTrue(payload.get("virtual_file", "").startswith("mem://"))
        self.assertTrue(payload.get("file_exists"))
        self.assertTrue(payload.get("file_isfile"))
        self.assertFalse(payload.get("file_isdir"))
        self.assertTrue(payload.get("dir_exists"))
        self.assertTrue(payload.get("dir_isdir"))
        self.assertFalse(payload.get("dir_isfile"))

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific bridge regression")
    def test_bridge_keeps_headful_init_alive_for_manual_login_or_challenge(self):
        repo_root = Path(__file__).resolve().parents[1]
        controller = f"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, {str(repo_root)!r})
from gemini_translator.api import config as api_config

repo_root = Path({str(repo_root)!r})
bridge_script = repo_root / "gemini_translator" / "scripts" / "chatgpt_workascii_bridge.cjs"
node_path = api_config.find_node_executable(repo_root)
if not node_path or not Path(node_path).exists():
    print(json.dumps({{"skip": "Bundled node runtime is not available"}}, ensure_ascii=False))
    raise SystemExit(0)

mock_playwright_index = r'''
const createPage = () => {{
  let phase = "cloudflare";
  let canAdvance = false;
  const locatorFor = (selector) => ({{
    count: async () => {{
      if (String(selector).includes("prompt-textarea")) return phase === "ready" ? 1 : 0;
      if (selector === "body") return 1;
      return 0;
    }},
    first() {{ return this; }},
    last() {{ return this; }},
    isVisible: async () => String(selector).includes("prompt-textarea") && phase === "ready",
    innerText: async () => {{
      if (selector !== "body") return "";
      if (phase === "cloudflare") {{
        canAdvance = true;
        return "Cloudflare challenge";
      }}
      if (phase === "login") return "Log in Continue with Google";
      return "ChatGPT";
    }},
    getAttribute: async () => null,
    isEnabled: async () => true,
    click: async () => {{}},
    hover: async () => {{}},
    scrollIntoViewIfNeeded: async () => {{}}
  }});
  return {{
    locator: (selector) => locatorFor(selector),
    getByRole: () => locatorFor("__missing__"),
    goto: async () => {{}},
    waitForTimeout: async () => {{
      if (!canAdvance) return;
      if (phase === "cloudflare") {{
        phase = "login";
        return;
      }}
      if (phase === "login") phase = "ready";
    }},
    url: () => (phase === "login" ? "https://auth.openai.com/login" : "https://chatgpt.com/"),
    content: async () => {{
      if (phase === "cloudflare") {{
        canAdvance = true;
        return "<html><body>challenge-platform</body></html>";
      }}
      return "<html><body>ok</body></html>";
    }},
    evaluate: async () => false,
    bringToFront: async () => {{}},
    keyboard: {{ press: async () => {{}}, insertText: async () => {{}} }},
    context: () => ({{ grantPermissions: async () => {{}} }})
  }};
}};
module.exports = {{
  chromium: {{
    launchPersistentContext: async () => {{
      const page = createPage();
      return {{
        grantPermissions: async () => {{}},
        pages: () => [page],
        newPage: async () => page,
        close: async () => {{}}
      }};
    }}
  }}
}};
'''

payload = {{}}
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    mock_root = tmp_path / "mock_playwright"
    mock_root.mkdir()
    (mock_root / "package.json").write_text(json.dumps({{"name": "playwright", "main": "index.js"}}), encoding="utf-8")
    (mock_root / "index.js").write_text(mock_playwright_index, encoding="utf-8")

    process = subprocess.Popen(
        [str(node_path), str(bridge_script)],
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        init_message = {{
            "type": "init",
            "config": {{
                "workascii_root": str(tmp_path),
                "profile_dir": str(tmp_path / "profile"),
                "playwright_package_root": str(mock_root),
                "browsers_path": "",
                "workspace_name": "",
                "workspace_index": 1,
                "headless": False,
                "timeout_sec": 60,
            }},
        }}
        process.stdin.write(json.dumps(init_message, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        init_line = process.stdout.readline().strip()
        stderr_line = process.stderr.readline().strip()

        process.stdin.write('{{"type":"shutdown"}}\\n')
        process.stdin.flush()
        shutdown_line = process.stdout.readline().strip()
        process.wait(timeout=10)

        payload = {{
            "init": json.loads(init_line),
            "shutdown": json.loads(shutdown_line),
            "stderr": stderr_line,
            "returncode": process.returncode,
        }}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

print(json.dumps(payload, ensure_ascii=False))
"""
        completed = subprocess.run(
            [sys.executable, "-c", controller],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        if payload.get("skip"):
            self.skipTest(payload["skip"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(payload["init"].get("ok"), payload)
        self.assertTrue(payload["shutdown"].get("ok"), payload)
        self.assertIn("manual browser action", payload["stderr"].lower(), payload)
        self.assertEqual(payload["returncode"], 0, payload)

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific bridge regression")
    def test_bridge_uses_distinct_pages_for_parallel_translate_commands(self):
        repo_root = Path(__file__).resolve().parents[1]
        controller = f"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, {str(repo_root)!r})
from gemini_translator.api import config as api_config

repo_root = Path({str(repo_root)!r})
bridge_script = repo_root / "gemini_translator" / "scripts" / "chatgpt_workascii_bridge.cjs"
node_path = api_config.find_node_executable(repo_root)
if not node_path or not Path(node_path).exists():
    print(json.dumps({{"skip": "Bundled node runtime is not available"}}, ensure_ascii=False))
    raise SystemExit(0)

mock_playwright_index = r'''
const pageStates = [];
const createPage = (id) => {{
  const state = {{
    id,
    prompt: "",
    generating: false,
    ticks: 0,
    response: "",
    closed: false,
    submitObservation: "unknown"
  }};
  pageStates.push(state);

  const locatorFor = (selector) => ({{
    count: async () => {{
      const text = String(selector);
      if (text.includes("prompt-textarea")) return 1;
      if (text === "body") return 1;
      if (text.includes("send-button") || text.includes("Send") || text.includes("Отправить")) return 1;
      if (text.includes("stop-button") || text.includes("Stop") || text.includes("Остановить")) {{
        return state.generating ? 1 : 0;
      }}
      if (text.includes("section[data-turn='assistant']") || text.includes("conversation-turn")) {{
        return state.response ? 1 : 0;
      }}
      return 0;
    }},
    first() {{ return this; }},
    last() {{ return this; }},
    locator: (nested) => locatorFor(nested),
    isVisible: async () => {{
      const text = String(selector);
      if (text.includes("prompt-textarea")) return true;
      if (text.includes("send-button") || text.includes("Send") || text.includes("Отправить")) return true;
      if (text.includes("stop-button") || text.includes("Stop") || text.includes("Остановить")) {{
        return state.generating;
      }}
      if (text.includes("section[data-turn='assistant']") || text.includes("conversation-turn")) {{
        return Boolean(state.response);
      }}
      return false;
    }},
    innerText: async () => {{
      if (String(selector) === "body") return "ChatGPT";
      return state.response || "";
    }},
    getAttribute: async () => null,
    isEnabled: async () => true,
    click: async () => {{
      const text = String(selector);
      if (text.includes("send-button") || text.includes("Send") || text.includes("Отправить")) {{
        if (state.id === "page-2") {{
          const pageOne = pageStates.find((candidate) => candidate.id === "page-1");
          state.submitObservation = pageOne && pageOne.generating ? "parallel" : "serial";
        }}
        state.generating = true;
        state.ticks = 0;
        state.response = "";
      }}
    }},
    fill: async (value) => {{
      state.prompt = String(value || "");
    }},
    hover: async () => {{}},
    scrollIntoViewIfNeeded: async () => {{}}
  }});

  return {{
    locator: (selector) => locatorFor(selector),
    getByRole: () => locatorFor("__missing__"),
    goto: async () => {{}},
    waitForTimeout: async (ms = 0) => {{
      if (state.generating && Number(ms) >= 1000) {{
        state.ticks += 1;
        const completionTicks = state.id === "page-1" ? 6 : 2;
        if (state.ticks >= completionTicks) {{
          state.generating = false;
          const suffix = state.id === "page-2" ? ` (${{state.submitObservation}})` : "";
          state.response = `response from ${{state.id}}${{suffix}}`;
        }}
      }}
    }},
    url: () => "https://chatgpt.com/",
    content: async () => "<html><body>ok</body></html>",
    evaluate: async () => false,
    bringToFront: async () => {{}},
    keyboard: {{
      press: async () => {{}},
      insertText: async (value) => {{
        state.prompt = String(value || "");
      }}
    }},
    context: () => ({{ grantPermissions: async () => {{}} }}),
    isClosed: () => state.closed,
    close: async () => {{
      state.closed = true;
    }}
  }};
}};

module.exports = {{
  chromium: {{
    launchPersistentContext: async () => {{
      const pages = [createPage("page-1")];
      return {{
        grantPermissions: async () => {{}},
        pages: () => pages.filter((page) => !page.isClosed()),
        newPage: async () => {{
          const page = createPage(`page-${{pages.length + 1}}`);
          pages.push(page);
          return page;
        }},
        close: async () => {{
          for (const page of pages) {{
            await page.close();
          }}
        }}
      }};
    }}
  }}
}};
'''

payload = {{}}
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    mock_root = tmp_path / "mock_playwright"
    mock_root.mkdir()
    (mock_root / "package.json").write_text(json.dumps({{"name": "playwright", "main": "index.js"}}), encoding="utf-8")
    (mock_root / "index.js").write_text(mock_playwright_index, encoding="utf-8")

    process = subprocess.Popen(
        [str(node_path), str(bridge_script)],
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        init_message = {{
            "id": "init-1",
            "type": "init",
            "config": {{
                "workascii_root": str(tmp_path),
                "profile_dir": str(tmp_path / "profile"),
                "playwright_package_root": str(mock_root),
                "browsers_path": "",
                "workspace_name": "",
                "workspace_index": 1,
                "headless": True,
                "timeout_sec": 60,
                "parallel_requests": 2,
            }},
        }}
        process.stdin.write(json.dumps(init_message, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        init_line = json.loads(process.stdout.readline().strip())

        process.stdin.write(json.dumps({{"id": "t1", "type": "translate", "prompt": "one", "system_instruction": ""}}, ensure_ascii=False) + "\\n")
        process.stdin.write(json.dumps({{"id": "t2", "type": "translate", "prompt": "two", "system_instruction": ""}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()

        response_lines = [
            json.loads(process.stdout.readline().strip()),
            json.loads(process.stdout.readline().strip()),
        ]

        process.stdin.write(json.dumps({{"id": "shutdown-1", "type": "shutdown"}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        shutdown_line = json.loads(process.stdout.readline().strip())
        process.wait(timeout=10)

        payload = {{
            "init": init_line,
            "responses": response_lines,
            "shutdown": shutdown_line,
            "stderr": process.stderr.read(),
            "returncode": process.returncode,
        }}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

print(json.dumps(payload, ensure_ascii=False))
"""
        completed = subprocess.run(
            [sys.executable, "-c", controller],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        if payload.get("skip"):
            self.skipTest(payload["skip"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(payload["init"].get("ok"), payload)
        self.assertTrue(payload["shutdown"].get("ok"), payload)
        self.assertEqual(payload["returncode"], 0, payload)

        response_by_id = {response["id"]: response for response in payload["responses"]}
        self.assertEqual(
            response_by_id["t1"].get("text"),
            "response from page-1",
            payload,
        )
        self.assertTrue(
            str(response_by_id["t2"].get("text", "")).startswith("response from page-2"),
            payload,
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific bridge regression")
    def test_bridge_waits_for_cloudflare_clear_during_translation_in_headful_mode(self):
        repo_root = Path(__file__).resolve().parents[1]
        controller = f"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, {str(repo_root)!r})
from gemini_translator.api import config as api_config

repo_root = Path({str(repo_root)!r})
bridge_script = repo_root / "gemini_translator" / "scripts" / "chatgpt_workascii_bridge.cjs"
node_path = api_config.find_node_executable(repo_root)
if not node_path or not Path(node_path).exists():
    print(json.dumps({{"skip": "Bundled node runtime is not available"}}, ensure_ascii=False))
    raise SystemExit(0)

mock_playwright_index = r'''
const state = {{
  prompt: "",
  response: "",
  assistantTurnCount: 0,
  sendClicks: 0,
  sendDisabled: false,
  phase: "ready",
  challengeTicks: 0,
  closed: false
}};

const locatorFor = (selector) => {{
  const text = String(selector);
  const isPrompt = text.includes("prompt-textarea");
  const isBody = text === "body";
  const isSend = text.includes("send-button") || text.includes("Send") || text.includes("Отправить");
  const isStop = text.includes("stop-button") || text.includes("Stop") || text.includes("Остановить");
  const isAssistant = text.includes("section[data-turn='assistant']") || text.includes("article[data-turn='assistant']") || text.includes("conversation-turn");
  const isCopy = text.includes("copy-turn-action-button") || text.includes("Copy");

  return {{
    count: async () => {{
      if (isPrompt || isBody || isSend) return 1;
      if (isStop) return 0;
      if (isAssistant) return state.assistantTurnCount;
      if (isCopy) return 0;
      return 0;
    }},
    first() {{ return this; }},
    last() {{ return this; }},
    locator: (nested) => locatorFor(nested),
    isVisible: async () => {{
      if (isPrompt || isBody || isSend) return true;
      if (isStop) return false;
      if (isAssistant) return state.assistantTurnCount > 0;
      return false;
    }},
    innerText: async () => {{
      if (isBody) {{
        if (state.phase === "challenge") return "Cloudflare challenge";
        return "ChatGPT";
      }}
      return state.response || "";
    }},
    getAttribute: async (name) => {{
      if (!isSend) return null;
      if (name === "disabled") return state.sendDisabled ? "" : null;
      if (name === "aria-disabled") return state.sendDisabled ? "true" : null;
      return null;
    }},
    isEnabled: async () => !state.sendDisabled,
    click: async () => {{
      if (isSend) {{
        state.sendClicks += 1;
        state.sendDisabled = true;
        state.phase = "challenge";
        state.challengeTicks = 0;
      }}
    }},
    fill: async (value) => {{
      state.prompt = String(value || "");
    }},
    hover: async () => {{}},
    scrollIntoViewIfNeeded: async () => {{}}
  }};
}};

const page = {{
  locator: (selector) => locatorFor(selector),
  getByRole: () => locatorFor("__missing__"),
  goto: async () => {{
    state.phase = "ready";
  }},
  waitForTimeout: async (ms = 0) => {{
    if (state.phase === "challenge" && Number(ms) >= 1000) {{
      state.challengeTicks += 1;
      if (state.challengeTicks >= 2) {{
        state.phase = "ready";
        state.sendDisabled = false;
        state.assistantTurnCount = 1;
        state.response = `challenge cleared (sendClicks=${{state.sendClicks}})`;
      }}
    }}
  }},
  url: () => "https://chatgpt.com/",
  content: async () => {{
    if (state.phase === "challenge") return "<html><body>challenge-platform</body></html>";
    return "<html><body>ok</body></html>";
  }},
  evaluate: async () => false,
  bringToFront: async () => {{}},
  keyboard: {{
    press: async () => {{}},
    insertText: async (value) => {{
      state.prompt = String(value || "");
    }}
  }},
  context: () => ({{ grantPermissions: async () => {{}} }}),
  isClosed: () => state.closed,
  close: async () => {{
    state.closed = true;
  }}
}};

module.exports = {{
  chromium: {{
    launchPersistentContext: async () => {{
      return {{
        grantPermissions: async () => {{}},
        pages: () => [page].filter((candidate) => !candidate.isClosed()),
        newPage: async () => page,
        close: async () => {{
          await page.close();
        }}
      }};
    }}
  }}
}};
'''

payload = {{}}
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    mock_root = tmp_path / "mock_playwright"
    mock_root.mkdir()
    (mock_root / "package.json").write_text(json.dumps({{"name": "playwright", "main": "index.js"}}), encoding="utf-8")
    (mock_root / "index.js").write_text(mock_playwright_index, encoding="utf-8")

    process = subprocess.Popen(
        [str(node_path), str(bridge_script)],
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        init_message = {{
            "id": "init-1",
            "type": "init",
            "config": {{
                "workascii_root": str(tmp_path),
                "profile_dir": str(tmp_path / "profile"),
                "playwright_package_root": str(mock_root),
                "browsers_path": "",
                "workspace_name": "",
                "workspace_index": 1,
                "headless": False,
                "timeout_sec": 60,
                "parallel_requests": 1,
            }},
        }}
        process.stdin.write(json.dumps(init_message, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        init_line = json.loads(process.stdout.readline().strip())

        process.stdin.write(json.dumps({{"id": "t1", "type": "translate", "prompt": "one", "system_instruction": ""}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        response_line = json.loads(process.stdout.readline().strip())

        process.stdin.write(json.dumps({{"id": "shutdown-1", "type": "shutdown"}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        shutdown_line = json.loads(process.stdout.readline().strip())
        process.wait(timeout=10)

        payload = {{
            "init": init_line,
            "response": response_line,
            "shutdown": shutdown_line,
            "stderr": process.stderr.read(),
            "returncode": process.returncode,
        }}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

print(json.dumps(payload, ensure_ascii=False))
"""
        completed = subprocess.run(
            [sys.executable, "-c", controller],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        if payload.get("skip"):
            self.skipTest(payload["skip"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(payload["init"].get("ok"), payload)
        self.assertTrue(payload["shutdown"].get("ok"), payload)
        self.assertEqual(payload["returncode"], 0, payload)
        self.assertEqual(payload["response"].get("text"), "challenge cleared (sendClicks=1)", payload)
        self.assertIn("cloudflare challenge detected", payload["stderr"].lower(), payload)

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific bridge regression")
    def test_bridge_does_not_resend_prompt_when_old_chat_is_open(self):
        repo_root = Path(__file__).resolve().parents[1]
        controller = f"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, {str(repo_root)!r})
from gemini_translator.api import config as api_config

repo_root = Path({str(repo_root)!r})
bridge_script = repo_root / "gemini_translator" / "scripts" / "chatgpt_workascii_bridge.cjs"
node_path = api_config.find_node_executable(repo_root)
if not node_path or not Path(node_path).exists():
    print(json.dumps({{"skip": "Bundled node runtime is not available"}}, ensure_ascii=False))
    raise SystemExit(0)

mock_playwright_index = r'''
const state = {{
  prompt: "",
  response: "stale response",
  assistantTurnCount: 1,
  sendClicks: 0,
  newChatClicks: 0,
  gotoCount: 0,
  sendDisabled: false,
  pendingResponse: false,
  urlValue: "https://chatgpt.com/c/stale",
  closed: false
}};

const locatorFor = (selector) => {{
  const text = String(selector);
  const isPrompt = text.includes("prompt-textarea");
  const isBody = text === "body";
  const isSend = text.includes("send-button") || text.includes("Send") || text.includes("Отправить");
  const isStop = text.includes("stop-button") || text.includes("Stop") || text.includes("Остановить");
  const isAssistant = text.includes("section[data-turn='assistant']") || text.includes("article[data-turn='assistant']") || text.includes("conversation-turn");
  const isNewChat = text.includes("__new_chat__") || text.includes("create-new-chat-button") || text === "a[href='/']";
  const isCopy = text.includes("copy-turn-action-button") || text.includes("Copy");

  return {{
    count: async () => {{
      if (isPrompt || isBody || isSend) return 1;
      if (isStop) return 0;
      if (isAssistant) return state.assistantTurnCount;
      if (isNewChat) return state.assistantTurnCount > 0 ? 1 : 0;
      if (isCopy) return 0;
      return 0;
    }},
    first() {{ return this; }},
    last() {{ return this; }},
    locator: (nested) => locatorFor(nested),
    isVisible: async () => {{
      if (isPrompt || isBody || isSend) return true;
      if (isStop) return false;
      if (isAssistant) return state.assistantTurnCount > 0;
      if (isNewChat) return state.assistantTurnCount > 0;
      return false;
    }},
    innerText: async () => {{
      if (isBody) return "ChatGPT";
      return state.response || "";
    }},
    getAttribute: async (name) => {{
      if (!isSend) return null;
      if (name === "disabled") return state.sendDisabled ? "" : null;
      if (name === "aria-disabled") return state.sendDisabled ? "true" : null;
      return null;
    }},
    isEnabled: async () => !state.sendDisabled,
    click: async () => {{
      if (isNewChat) {{
        state.newChatClicks += 1;
        state.response = "";
        state.urlValue = "https://chatgpt.com/";
        return;
      }}

      if (isSend) {{
        state.sendClicks += 1;
        state.sendDisabled = true;
        state.pendingResponse = true;
      }}
    }},
    fill: async (value) => {{
      state.prompt = String(value || "");
    }},
    hover: async () => {{}},
    scrollIntoViewIfNeeded: async () => {{}}
  }};
}};

const page = {{
  locator: (selector) => locatorFor(selector),
  getByRole: (_role, options = {{}}) => {{
    const name = String(options.name || "");
    if (/New chat/i.test(name)) {{
      return locatorFor("__new_chat__");
    }}
    return locatorFor("__missing__");
  }},
  goto: async () => {{
    state.gotoCount += 1;
    state.urlValue = "https://chatgpt.com/c/stale";
  }},
  waitForTimeout: async (ms = 0) => {{
    if (state.pendingResponse && Number(ms) >= 1000) {{
      state.pendingResponse = false;
      state.sendDisabled = false;
      state.assistantTurnCount += 1;
      state.response = `fresh response (sendClicks=${{state.sendClicks}}, newChat=${{state.newChatClicks}}, goto=${{state.gotoCount}})`;
    }}
  }},
  url: () => state.urlValue,
  content: async () => "<html><body>ok</body></html>",
  evaluate: async () => false,
  bringToFront: async () => {{}},
  keyboard: {{
    press: async () => {{}},
    insertText: async (value) => {{
      state.prompt = String(value || "");
    }}
  }},
  context: () => ({{ grantPermissions: async () => {{}} }}),
  isClosed: () => state.closed,
  close: async () => {{
    state.closed = true;
  }}
}};

module.exports = {{
  chromium: {{
    launchPersistentContext: async () => {{
      return {{
        grantPermissions: async () => {{}},
        pages: () => [page].filter((candidate) => !candidate.isClosed()),
        newPage: async () => page,
        close: async () => {{
          await page.close();
        }}
      }};
    }}
  }}
}};
'''

payload = {{}}
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    mock_root = tmp_path / "mock_playwright"
    mock_root.mkdir()
    (mock_root / "package.json").write_text(json.dumps({{"name": "playwright", "main": "index.js"}}), encoding="utf-8")
    (mock_root / "index.js").write_text(mock_playwright_index, encoding="utf-8")

    process = subprocess.Popen(
        [str(node_path), str(bridge_script)],
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        init_message = {{
            "id": "init-1",
            "type": "init",
            "config": {{
                "workascii_root": str(tmp_path),
                "profile_dir": str(tmp_path / "profile"),
                "playwright_package_root": str(mock_root),
                "browsers_path": "",
                "workspace_name": "",
                "workspace_index": 1,
                "headless": True,
                "timeout_sec": 60,
                "parallel_requests": 1,
            }},
        }}
        process.stdin.write(json.dumps(init_message, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        init_line = json.loads(process.stdout.readline().strip())

        process.stdin.write(json.dumps({{"id": "t1", "type": "translate", "prompt": "one", "system_instruction": ""}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        response_line = json.loads(process.stdout.readline().strip())

        process.stdin.write(json.dumps({{"id": "shutdown-1", "type": "shutdown"}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        shutdown_line = json.loads(process.stdout.readline().strip())
        process.wait(timeout=10)

        payload = {{
            "init": init_line,
            "response": response_line,
            "shutdown": shutdown_line,
            "stderr": process.stderr.read(),
            "returncode": process.returncode,
        }}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

print(json.dumps(payload, ensure_ascii=False))
"""
        completed = subprocess.run(
            [sys.executable, "-c", controller],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        if payload.get("skip"):
            self.skipTest(payload["skip"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(payload["init"].get("ok"), payload)
        self.assertTrue(payload["shutdown"].get("ok"), payload)
        self.assertEqual(payload["returncode"], 0, payload)
        self.assertEqual(
            payload["response"].get("text"),
            "fresh response (sendClicks=1, newChat=1, goto=1)",
            payload,
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows-specific bridge regression")
    def test_bridge_uses_direct_prompt_without_file_upload(self):
        repo_root = Path(__file__).resolve().parents[1]
        controller = f"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, {str(repo_root)!r})
from gemini_translator.api import config as api_config

repo_root = Path({str(repo_root)!r})
bridge_script = repo_root / "gemini_translator" / "scripts" / "chatgpt_workascii_bridge.cjs"
node_path = api_config.find_node_executable(repo_root)
if not node_path or not Path(node_path).exists():
    print(json.dumps({{"skip": "Bundled node runtime is not available"}}, ensure_ascii=False))
    raise SystemExit(0)

mock_playwright_index = r'''
const path = require("path");
const state = {{
  prompt: "",
  response: "",
  assistantTurnCount: 0,
  sendClicks: 0,
  sendDisabled: false,
  pendingResponse: false,
  uploadFailed: false,
  uploadedFile: "",
  closed: false
}};

const locatorFor = (selector) => {{
  const text = String(selector);
  const isPrompt = text.includes("prompt-textarea");
  const isBody = text === "body";
  const isSend = text.includes("send-button") || text.includes("Send") || text.includes("Отправить");
  const isStop = text.includes("stop-button") || text.includes("Stop") || text.includes("Остановить");
  const isAssistant = text.includes("section[data-turn='assistant']") || text.includes("article[data-turn='assistant']") || text.includes("conversation-turn");
  const isCopy = text.includes("copy-turn-action-button") || text.includes("Copy");
  const isFileInput = text === "input[type='file']";

  return {{
    count: async () => {{
      if (isFileInput) return 1;
      if (isPrompt || isBody || isSend) return 1;
      if (isStop || isCopy) return 0;
      if (isAssistant) return state.assistantTurnCount;
      return 0;
    }},
    first() {{ return this; }},
    last() {{ return this; }},
    nth() {{ return this; }},
    filter() {{ return this; }},
    locator: (nested) => locatorFor(nested),
    isVisible: async () => {{
      if (isFileInput) return true;
      if (isPrompt || isBody || isSend) return true;
      if (isStop || isCopy) return false;
      if (isAssistant) return state.assistantTurnCount > 0;
      return false;
    }},
    innerText: async () => {{
      if (isBody) {{
        return state.uploadFailed
          ? "ChatGPT\\n\\u041d\\u0435 \\u0443\\u0434\\u0430\\u043b\\u043e\\u0441\\u044c \\u0437\\u0430\\u0433\\u0440\\u0443\\u0437\\u0438\\u0442\\u044c \\u0444\\u0430\\u0439\\u043b \\u043d\\u0430 \\u0441\\u0430\\u0439\\u0442 files.oaiusercontent.com."
          : "ChatGPT";
      }}
      if (isAssistant) return state.response || "";
      if (isPrompt) return state.prompt;
      return "";
    }},
    getAttribute: async (name) => {{
      if (isFileInput && name === "accept") return ".txt";
      if (!isSend) return null;
      if (name === "disabled") return state.sendDisabled ? "" : null;
      if (name === "aria-disabled") return state.sendDisabled ? "true" : null;
      return null;
    }},
    isEnabled: async () => !state.sendDisabled,
    evaluate: async (fn) => {{
      if (isFileInput) {{
        return fn({{ getAttribute: (name) => (name === "accept" ? ".txt" : "") }});
      }}
      if (isPrompt) return state.prompt;
      return false;
    }},
    setInputFiles: async (files) => {{
      const first = Array.isArray(files) ? files[0] : files;
      state.uploadedFile = path.basename(String(first || ""));
      state.uploadFailed = true;
    }},
    click: async () => {{
      if (isSend) {{
        state.sendClicks += 1;
        state.sendDisabled = true;
        state.pendingResponse = true;
      }}
    }},
    fill: async (value) => {{
      state.prompt = String(value || "");
    }},
    hover: async () => {{}},
    scrollIntoViewIfNeeded: async () => {{}}
  }};
}};

const page = {{
  locator: (selector) => locatorFor(selector),
  getByRole: () => locatorFor("__missing__"),
  goto: async () => {{}},
  waitForTimeout: async (ms = 0) => {{
    if (state.pendingResponse && Number(ms) >= 1000) {{
      state.pendingResponse = false;
      state.sendDisabled = false;
      state.assistantTurnCount += 1;
      state.response = state.uploadedFile ? "FILE_UPLOAD_ATTEMPTED" : "DIRECT_TEXT";
    }}
  }},
  url: () => "https://chatgpt.com/",
  content: async () => "<html><body>ok</body></html>",
  evaluate: async () => false,
  bringToFront: async () => {{}},
  keyboard: {{
    press: async () => {{}},
    insertText: async (value) => {{
      state.prompt = String(value || "");
    }}
  }},
  context: () => ({{ grantPermissions: async () => {{}} }}),
  isClosed: () => state.closed,
  close: async () => {{
    state.closed = true;
  }}
}};

module.exports = {{
  chromium: {{
    launchPersistentContext: async () => {{
      return {{
        grantPermissions: async () => {{}},
        pages: () => [page].filter((candidate) => !candidate.isClosed()),
        newPage: async () => page,
        close: async () => {{
          await page.close();
        }}
      }};
    }}
  }}
}};
'''

payload = {{}}
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    mock_root = tmp_path / "mock_playwright"
    mock_root.mkdir()
    (mock_root / "package.json").write_text(json.dumps({{"name": "playwright", "main": "index.js"}}), encoding="utf-8")
    (mock_root / "index.js").write_text(mock_playwright_index, encoding="utf-8")

    process = subprocess.Popen(
        [str(node_path), str(bridge_script)],
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        init_message = {{
            "id": "init-1",
            "type": "init",
            "config": {{
                "workascii_root": str(tmp_path),
                "profile_dir": str(tmp_path / "profile"),
                "playwright_package_root": str(mock_root),
                "browsers_path": "",
                "workspace_name": "",
                "workspace_index": 1,
                "headless": True,
                "timeout_sec": 60,
                "parallel_requests": 1,
            }},
        }}
        process.stdin.write(json.dumps(init_message, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        init_line = json.loads(process.stdout.readline().strip())

        process.stdin.write(json.dumps({{"id": "t1", "type": "translate", "prompt": "one", "system_instruction": ""}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        response_line = json.loads(process.stdout.readline().strip())

        process.stdin.write(json.dumps({{"id": "shutdown-1", "type": "shutdown"}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        shutdown_line = json.loads(process.stdout.readline().strip())
        process.wait(timeout=10)

        payload = {{
            "init": init_line,
            "response": response_line,
            "shutdown": shutdown_line,
            "stderr": process.stderr.read(),
            "returncode": process.returncode,
        }}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

print(json.dumps(payload, ensure_ascii=False))
"""
        completed = subprocess.run(
            [sys.executable, "-c", controller],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        if payload.get("skip"):
            self.skipTest(payload["skip"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(payload["init"].get("ok"), payload)
        self.assertTrue(payload["shutdown"].get("ok"), payload)
        self.assertEqual(payload["returncode"], 0, payload)
        self.assertEqual(payload["response"].get("text"), "DIRECT_TEXT", payload)
        self.assertNotIn("falling back to direct composer text", payload["stderr"].lower(), payload)

    def test_bridge_ignores_prompt_echo_from_copy_button_until_real_response_arrives(self):
        repo_root = Path(__file__).resolve().parents[1]
        controller = f"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, {str(repo_root)!r})
from gemini_translator.api import config as api_config

repo_root = Path({str(repo_root)!r})
bridge_script = repo_root / "gemini_translator" / "scripts" / "chatgpt_workascii_bridge.cjs"
node_path = api_config.find_node_executable(repo_root)
if not node_path or not Path(node_path).exists():
    print(json.dumps({{"skip": "Bundled node runtime is not available"}}, ensure_ascii=False))
    raise SystemExit(0)

mock_playwright_index = r'''
const state = {{
  prompt: "",
  response: "",
  clipboard: "",
  assistantTurnCount: 0,
  sendClicks: 0,
  copyClicks: 0,
  sendDisabled: false,
  pendingResponse: false,
  responseReadyTicks: 0,
  closed: false
}};

const locatorFor = (selector) => {{
  const text = String(selector);
  const isPrompt = text.includes("prompt-textarea");
  const isBody = text === "body";
  const isSend = text.includes("send-button") || text.includes("Send") || text.includes("Отправить");
  const isStop = text.includes("stop-button") || text.includes("Stop") || text.includes("Остановить");
  const isAssistant = text.includes("section[data-turn='assistant']") || text.includes("article[data-turn='assistant']") || text.includes("conversation-turn");
  const isCopy = text.includes("copy-turn-action-button") || text.includes("Copy");

  return {{
    count: async () => {{
      if (isPrompt || isBody || isSend) return 1;
      if (isStop) return 0;
      if (isAssistant) return state.assistantTurnCount;
      if (isCopy) return state.assistantTurnCount > 0 ? 1 : 0;
      return 0;
    }},
    first() {{ return this; }},
    last() {{ return this; }},
    locator: (nested) => locatorFor(nested),
    isVisible: async () => {{
      if (isPrompt || isBody || isSend) return true;
      if (isStop) return false;
      if (isAssistant) return state.assistantTurnCount > 0;
      if (isCopy) return state.assistantTurnCount > 0;
      return false;
    }},
    innerText: async () => {{
      if (isBody) return "ChatGPT";
      return state.response || "";
    }},
    getAttribute: async (name) => {{
      if (!isSend) return null;
      if (name === "disabled") return state.sendDisabled ? "" : null;
      if (name === "aria-disabled") return state.sendDisabled ? "true" : null;
      return null;
    }},
    isEnabled: async () => !state.sendDisabled,
    click: async () => {{
      if (isSend) {{
        state.sendClicks += 1;
        state.sendDisabled = true;
        state.pendingResponse = true;
        state.assistantTurnCount = 1;
        state.responseReadyTicks = 0;
        state.response = state.prompt;
        return;
      }}

      if (isCopy) {{
        state.copyClicks += 1;
        state.clipboard = state.response;
      }}
    }},
    fill: async (value) => {{
      state.prompt = String(value || "");
    }},
    hover: async () => {{}},
    scrollIntoViewIfNeeded: async () => {{}}
  }};
}};

const page = {{
  locator: (selector) => locatorFor(selector),
  getByRole: () => locatorFor("__missing__"),
  goto: async () => {{}},
  waitForTimeout: async (ms = 0) => {{
    if (state.pendingResponse && Number(ms) >= 1000) {{
      state.responseReadyTicks += 1;
      if (state.responseReadyTicks >= 3) {{
        state.pendingResponse = false;
        state.sendDisabled = false;
        state.response = "ok";
      }}
    }}
  }},
  url: () => "https://chatgpt.com/",
  content: async () => "<html><body>ok</body></html>",
  evaluate: async () => state.clipboard,
  bringToFront: async () => {{}},
  keyboard: {{
    press: async () => {{}},
    insertText: async (value) => {{
      state.prompt = String(value || "");
    }}
  }},
  context: () => ({{ grantPermissions: async () => {{}} }}),
  isClosed: () => state.closed,
  close: async () => {{
    state.closed = true;
  }}
}};

module.exports = {{
  chromium: {{
    launchPersistentContext: async () => {{
      return {{
        grantPermissions: async () => {{}},
        pages: () => [page].filter((candidate) => !candidate.isClosed()),
        newPage: async () => page,
        close: async () => {{
          await page.close();
        }}
      }};
    }}
  }}
}};
'''

payload = {{}}
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    mock_root = tmp_path / "mock_playwright"
    mock_root.mkdir()
    (mock_root / "package.json").write_text(json.dumps({{"name": "playwright", "main": "index.js"}}), encoding="utf-8")
    (mock_root / "index.js").write_text(mock_playwright_index, encoding="utf-8")

    process = subprocess.Popen(
        [str(node_path), str(bridge_script)],
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        init_message = {{
            "id": "init-1",
            "type": "init",
            "config": {{
                "workascii_root": str(tmp_path),
                "profile_dir": str(tmp_path / "profile"),
                "playwright_package_root": str(mock_root),
                "browsers_path": "",
                "workspace_name": "",
                "workspace_index": 1,
                "headless": True,
                "timeout_sec": 60,
                "parallel_requests": 1,
            }},
        }}
        process.stdin.write(json.dumps(init_message, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        init_line = json.loads(process.stdout.readline().strip())

        process.stdin.write(json.dumps({{"id": "t1", "type": "translate", "prompt": "very long prompt text", "system_instruction": ""}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        response_line = json.loads(process.stdout.readline().strip())

        process.stdin.write(json.dumps({{"id": "shutdown-1", "type": "shutdown"}}, ensure_ascii=False) + "\\n")
        process.stdin.flush()
        shutdown_line = json.loads(process.stdout.readline().strip())
        process.wait(timeout=10)

        payload = {{
            "init": init_line,
            "response": response_line,
            "shutdown": shutdown_line,
            "stderr": process.stderr.read(),
            "returncode": process.returncode,
        }}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

print(json.dumps(payload, ensure_ascii=False))
"""
        completed = subprocess.run(
            [sys.executable, "-c", controller],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        if payload.get("skip"):
            self.skipTest(payload["skip"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(payload["init"].get("ok"), payload)
        self.assertTrue(payload["shutdown"].get("ok"), payload)
        self.assertEqual(payload["returncode"], 0, payload)
        self.assertEqual(payload["response"].get("text"), "ok", payload)

    def test_handler_uses_full_init_timeout_in_headful_mode(self):
        class DummyWorker:
            provider_config = {}
            prompt_builder = type("PromptBuilder", (), {"system_instruction": ""})()
            workascii_workspace_name = ""
            workascii_workspace_index = 1
            workascii_timeout_sec = 1800
            workascii_headless = False

        handler = WorkAsciiChatGptApiHandler(DummyWorker())
        handler.timeout_sec = 1800
        handler.headless = False
        self.assertEqual(handler._get_bridge_init_timeout(), 1800)

        handler.headless = True
        self.assertEqual(handler._get_bridge_init_timeout(), 120)

    def test_default_runtime_root_prefers_project_root_over_legacy_workascii(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            workascii_root = tmp_path / "work_ascii"
            workascii_root.mkdir()
            project_root = tmp_path / "src"
            project_root.mkdir()
            dist_root = tmp_path / "dist"
            dist_root.mkdir()

            with patch.object(api_config, "find_workascii_root", return_value=workascii_root), \
                 patch.object(api_config, "get_executable_dir", return_value=dist_root), \
                 patch.object(api_config, "get_internal_resource_dir", return_value=None), \
                 patch.object(api_config, "get_dev_project_root", return_value=project_root):
                self.assertEqual(api_config.default_workascii_runtime_root(), dist_root)

    def test_find_playwright_package_root_finds_bundled_runtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(tmpdir)
            package_root = runtime_root / "playwright_runtime" / "package"
            external_package_root = runtime_root / "external-playwright"
            package_root.mkdir(parents=True)
            external_package_root.mkdir(parents=True)
            (package_root / "package.json").write_text("{}", encoding="utf-8")
            (external_package_root / "package.json").write_text("{}", encoding="utf-8")

            with patch.object(api_config, "get_executable_dir", return_value=runtime_root), \
                 patch.object(api_config, "get_internal_resource_dir", return_value=None), \
                 patch.object(api_config, "_python_playwright_driver_dir", return_value=None), \
                 patch.dict(api_config.os.environ, {"PLAYWRIGHT_PACKAGE_ROOT": str(external_package_root)}, clear=True):
                self.assertEqual(api_config.find_playwright_package_root(), package_root)

    def test_find_node_and_browser_cache_use_bundled_runtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(tmpdir)
            node_path = runtime_root / "playwright_runtime" / "node.exe"
            browsers_path = runtime_root / "playwright_runtime" / "ms-playwright"
            external_node = runtime_root / "system-node.exe"
            external_browsers = runtime_root / "external-ms-playwright"
            node_path.parent.mkdir(parents=True)
            browsers_path.mkdir(parents=True)
            node_path.write_bytes(b"node")
            external_node.write_bytes(b"node")
            external_browsers.mkdir(parents=True)

            with patch.object(api_config, "get_executable_dir", return_value=runtime_root), \
                 patch.object(api_config, "get_internal_resource_dir", return_value=None), \
                 patch.object(api_config, "_python_playwright_driver_dir", return_value=None), \
                 patch.object(api_config.shutil, "which", return_value=str(external_node)), \
                 patch.dict(
                     api_config.os.environ,
                     {"PLAYWRIGHT_BROWSERS_PATH": str(external_browsers)},
                     clear=True,
                    ):
                self.assertEqual(api_config.find_node_executable(), node_path)
                self.assertEqual(api_config.find_playwright_browsers_path(), browsers_path)

    def test_runtime_helpers_fall_back_to_dev_project_runtime_when_legacy_root_has_no_runtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            legacy_root = tmp_path / "work_ascii"
            dev_root = tmp_path / "translatorFork"
            package_root = dev_root / "playwright_runtime" / "package"
            node_path = dev_root / "playwright_runtime" / "node.exe"
            browsers_path = dev_root / "playwright_runtime" / "ms-playwright"

            legacy_root.mkdir()
            package_root.mkdir(parents=True)
            browsers_path.mkdir(parents=True)
            node_path.write_bytes(b"node")
            (package_root / "package.json").write_text("{}", encoding="utf-8")

            with patch.object(api_config, "get_executable_dir", return_value=None), \
                 patch.object(api_config, "get_internal_resource_dir", return_value=None), \
                 patch.object(api_config, "get_dev_project_root", return_value=dev_root), \
                 patch.object(api_config, "_python_playwright_driver_dir", return_value=None), \
                 patch.object(api_config.shutil, "which", return_value=None), \
                 patch.dict(api_config.os.environ, {}, clear=True):
                self.assertEqual(api_config.find_node_executable(legacy_root), node_path)
                self.assertEqual(api_config.find_playwright_package_root(legacy_root), package_root)
                self.assertEqual(api_config.find_playwright_browsers_path(legacy_root), browsers_path)

    def test_handler_ignores_saved_path_overrides_and_uses_project_runtime(self):
        class DummyWorker:
            provider_config = {}
            workascii_root = r"C:\legacy\work_ascii"
            workascii_profile_dir = r"C:\legacy\profile"
            workascii_node_path = r"C:\legacy\node.exe"
            workascii_workspace_name = ""
            workascii_workspace_index = 1
            workascii_timeout_sec = 1800
            workascii_headless = False

        project_root = Path(r"C:\project-runtime")
        profile_dir = project_root / "chatgpt-profile-run"
        node_path = project_root / "playwright_runtime" / "node.exe"
        package_root = project_root / "playwright_runtime" / "package"
        browsers_root = project_root / "playwright_runtime" / "ms-playwright"

        handler = WorkAsciiChatGptApiHandler(DummyWorker())

        with patch.object(api_config, "default_workascii_runtime_root", return_value=project_root), \
             patch.object(api_config, "default_workascii_profile_dir", return_value=profile_dir), \
             patch.object(api_config, "find_node_executable", return_value=node_path), \
             patch.object(api_config, "find_playwright_package_root", return_value=package_root), \
             patch.object(api_config, "find_playwright_browsers_path", return_value=browsers_root), \
             patch.object(api_config, "get_resource_path", return_value=project_root / "bridge.cjs"):
            handler.setup_client()

        self.assertEqual(handler.workascii_root, project_root)
        self.assertEqual(handler.profile_dir, profile_dir)
        self.assertEqual(handler.node_path, node_path)
        self.assertEqual(handler.playwright_package_root, package_root)
        self.assertEqual(handler.playwright_browsers_path, browsers_root)

    def test_handler_preserves_configured_parallel_requests(self):
        class DummyWorker:
            provider_config = {}
            workascii_workspace_name = ""
            workascii_workspace_index = 1
            workascii_timeout_sec = 1800
            workascii_headless = False
            workascii_refresh_every_requests = 0
            max_concurrent_requests = 3

        project_root = Path(r"C:\project-runtime")
        profile_dir = project_root / "chatgpt-profile-run"
        node_path = project_root / "playwright_runtime" / "node.exe"
        package_root = project_root / "playwright_runtime" / "package"
        browsers_root = project_root / "playwright_runtime" / "ms-playwright"

        handler = WorkAsciiChatGptApiHandler(DummyWorker())

        with patch.object(api_config, "default_workascii_runtime_root", return_value=project_root), \
             patch.object(api_config, "default_workascii_profile_dir", return_value=profile_dir), \
             patch.object(api_config, "find_node_executable", return_value=node_path), \
             patch.object(api_config, "find_playwright_package_root", return_value=package_root), \
             patch.object(api_config, "find_playwright_browsers_path", return_value=browsers_root), \
             patch.object(api_config, "get_resource_path", return_value=project_root / "bridge.cjs"):
            handler.setup_client()

        self.assertEqual(handler.parallel_requests, 3)

    def test_profile_template_replaces_runtime_profile_before_bridge_launch(self):
        class DummyWorker:
            provider_config = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            template_dir = tmp_path / "template-profile"
            runtime_dir = tmp_path / "runtime-profile"

            (template_dir / "Default").mkdir(parents=True)
            (template_dir / "Default" / "Preferences").write_text('{"fresh": true}', encoding="utf-8")

            runtime_dir.mkdir(parents=True)
            (runtime_dir / "stale.txt").write_text("old", encoding="utf-8")

            handler = WorkAsciiChatGptApiHandler(DummyWorker())
            handler.profile_dir = runtime_dir
            handler.profile_template_dir = template_dir

            handler._prepare_profile_dir_for_launch_sync()

            self.assertFalse((runtime_dir / "stale.txt").exists())
            self.assertEqual(
                (runtime_dir / "Default" / "Preferences").read_text(encoding="utf-8"),
                '{"fresh": true}',
            )

    def test_bridge_subprocess_uses_large_pipe_limit_for_long_responses(self):
        class DummyWorker:
            provider_config = {}
            workascii_workspace_name = ""
            workascii_workspace_index = 1
            workascii_timeout_sec = 1800
            workascii_headless = False
            workascii_refresh_every_requests = 0

        class DummyProcess:
            returncode = None
            stdin = None
            stdout = None
            stderr = None

        async def scenario():
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                node_path = tmp_path / "node.exe"
                package_root = tmp_path / "package"
                bridge_path = tmp_path / "bridge.cjs"
                profile_dir = tmp_path / "profile"
                browsers_path = tmp_path / "browsers"

                node_path.write_bytes(b"node")
                package_root.mkdir()
                (package_root / "package.json").write_text("{}", encoding="utf-8")
                bridge_path.write_text("", encoding="utf-8")
                browsers_path.mkdir()

                handler = WorkAsciiChatGptApiHandler(DummyWorker())
                handler.profile_dir = profile_dir
                handler.execution_cwd = tmp_path
                handler.node_path = node_path
                handler.bridge_script_path = bridge_path
                handler.playwright_package_root = package_root
                handler.playwright_browsers_path = browsers_path
                handler.workascii_root = tmp_path
                handler.timeout_sec = 1800
                handler.headless = False
                handler.parallel_requests = 1

                with patch.object(handler, "_prepare_profile_dir_for_launch", new=AsyncMock()), \
                     patch.object(handler, "_send_command", new=AsyncMock(return_value={"ok": True})), \
                     patch(
                         "gemini_translator.api.handlers.workascii_chatgpt.asyncio.create_subprocess_exec",
                         new=AsyncMock(return_value=DummyProcess()),
                     ) as create_process:
                    await handler._ensure_bridge_ready()

                self.assertEqual(create_process.await_args.kwargs.get("limit"), 16 * 1024 * 1024)

        asyncio.run(scenario())

    def test_call_api_restarts_bridge_after_refresh_budget(self):
        class DummyWorker:
            provider_config = {"base_timeout": 120}
            prompt_builder = type("PromptBuilder", (), {"system_instruction": ""})()
            workascii_workspace_name = ""
            workascii_workspace_index = 1
            workascii_timeout_sec = 1800
            workascii_headless = False
            workascii_refresh_every_requests = 2

            def _post_event(self, *_args, **_kwargs):
                return None

        handler = WorkAsciiChatGptApiHandler(DummyWorker())
        handler.refresh_every_requests = 2
        handler.bridge_script_path = Path("bridge.cjs")

        async def scenario():
            with patch.object(handler, "_ensure_bridge_ready", new=AsyncMock()) as ensure_ready, \
                 patch.object(handler, "_send_command", new=AsyncMock(return_value={"ok": True, "text": "ok"})) as send_command, \
                 patch.object(handler, "_terminate_bridge", new=AsyncMock()) as terminate_bridge:
                self.assertEqual(await handler.call_api("one", "log-1"), "ok")
                self.assertEqual(await handler.call_api("two", "log-2"), "ok")

                terminate_bridge.assert_not_awaited()
                self.assertTrue(handler._bridge_restart_pending)
                self.assertEqual(handler._successful_calls_since_refresh, 0)

                self.assertEqual(await handler.call_api("three", "log-3"), "ok")

                terminate_bridge.assert_awaited_once()
                self.assertEqual(send_command.await_count, 3)
                self.assertEqual(ensure_ready.await_count, 3)
                self.assertFalse(handler._bridge_restart_pending)
                self.assertEqual(handler._successful_calls_since_refresh, 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
