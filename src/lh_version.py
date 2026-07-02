# lh_version.py — single source of truth for the app version.
# build_exe.py / build_linux.py read APP_VERSION out of this file via regex
# (without importing it, to avoid pulling in GUI deps during the build step).

APP_NAME = "Lingo Hunter AI"
APP_VERSION = "1.1.0"
