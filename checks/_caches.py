"""Shared helper: ~/.cache entries that are safe to delete (the application rebuilds them)."""
# JetBrains is left out on purpose: the current IDE indexes live there; old IDE versions are handled by jetbrains-old.
SAFE_CACHES = (
    "yay", "pip", "npm", "yarn", "uv", "go-build", "google-chrome", "chromium", "mozilla",
    "mesa_shader_cache", "mesa_shader_cache_db", "electron", "node-gyp",
)
