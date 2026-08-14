"""Shared PyInstaller inputs for modules loaded through ``importlib``."""

LAZY_HANDLER_HIDDEN_IMPORTS = [
    "gemini_translator.api.handlers.browser",
    "gemini_translator.api.handlers.dry_run",
    "gemini_translator.api.handlers.gemini",
    "gemini_translator.api.handlers.huggingface",
    "gemini_translator.api.handlers.deepseek",
    "gemini_translator.api.handlers.nvidia",
    "gemini_translator.api.handlers.openmodel",
    "gemini_translator.api.handlers.local",
    "gemini_translator.api.handlers.mcp",
    "gemini_translator.api.handlers.openrouter",
    "gemini_translator.api.handlers.qoder",
    "gemini_translator.api.handlers.workascii_chatgpt",
]

LAZY_SERVER_HIDDEN_IMPORTS = [
    "gemini_translator.api.servers.perplexity",
]
