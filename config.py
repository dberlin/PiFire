"""This file contains configuration settings for the application."""

import os

from common.common import LOG_DIR


class Config:
    BACKUP_PATH = "./backups/"  # Settings/pellet-DB backups exported from SQLite, plus their manifest.json
    UPLOAD_FOLDER = BACKUP_PATH  # Point uploads to the backup path
    HISTORY_FOLDER = "./history/"  # Path to historical cook files
    RECIPE_FOLDER = "./recipes/"  # Path to recipe files
    #  Derived, so the directory Flask serves logs FROM is by construction the
    #  directory logging writes TO. They were two independent literals, which
    #  meant redirecting one silently left the other pointing at ./logs/.
    LOGS_FOLDER = os.path.join(LOG_DIR, "")  # Path to log files (trailing separator)
    ALLOWED_EXTENSIONS = {"json", "pifire", "pfrecipe", "jpg", "jpeg", "png", "gif", "bmp", "log"}


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
