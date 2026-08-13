"""PiFire common package.

Public names live in dedicated submodules -- import them from their real homes:
``common.common`` (bottom-layer utilities), ``common.defaults``,
the domain modules under ``common.persistence``, ``common.settings_migration``,
``common.system``, ``common.api_commands`` and ``common.backups``. This package
re-exports nothing (no ``from common.common import *`` facade).
"""
