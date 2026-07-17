"""PiFire common package.

Public names live in dedicated submodules -- import them from their real homes:
``common.common`` (bottom-layer utilities), ``common.defaults``,
``common.datastore_accessors``, ``common.settings_migration``, ``common.system``,
``common.api_commands`` and ``common.backups``. This package intentionally
re-exports nothing (no ``from common.common import *`` facade).
"""
