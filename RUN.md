# Quick Start

## Windows

Run `run.bat`.

## Linux

Run:

```bash
chmod +x run.sh
./run.sh
```

If you plan to use Playwright-based tools such as ChatGPT Web automation or RanobeLib uploader, install the browser once after the virtual environment is created:

```bash
.venv/bin/python -m playwright install chromium
```

For the ChatGPT Web browser mode, `node` must also be available in `PATH`.

If your distro does not have `python3-venv`, install it first with your system package manager.
