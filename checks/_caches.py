"""Shared helper: ~/.cache entries that are safe to delete (the application rebuilds them)."""
SAFE_CACHES = (
    "yay", "pip", "npm", "yarn", "uv", "go-build", "google-chrome", "chromium", "mozilla",
    "mesa_shader_cache", "mesa_shader_cache_db", "JetBrains", "electron", "node-gyp",
)
