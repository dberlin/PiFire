"""
==============================================================================
 PiFire Backups
==============================================================================

Description: Backup/restore of the settings and pellet databases, plus the
  pelletdb.json FILE reader whose corrupt-file self-repair path calls back
  into the pellet DB restore.

  Extracted from common/common.py. These functions sit ABOVE
  common/datastore_accessors.py (they read/write the live SQLite state in
  order to back it up), which is why they cannot stay in common/common.py:
  common/common.py is the bottom utility layer that datastore_accessors
  imports from, so leaving them there means the bottom layer calls upward --
  a module-level cycle the moment Task 9 deletes the compatibility facade.

  common/common.py re-imports these names for now so that existing
  `common.common.X` call sites keep resolving.

==============================================================================
"""

import datetime
import json
import os

from common.common import BACKUP_PATH, read_generic_json, write_generic_json, write_log
from common.datastore_accessors import (
    read_pellet_db,
    read_settings,
    write_pellet_db,
    write_warning,
)
from common.defaults import default_pellets


def backup_settings():
    # Write the CURRENT settings (SQLite is the source of truth at runtime, the
    # settings.json file is not kept in sync) to a backup copy in
    # /[BACKUP_PATH]/PiFire_[DATE]_[TIME].json
    time_now = datetime.datetime.now()
    time_str = time_now.strftime("%m-%d-%y_%H%M%S")  # Truncate the microseconds
    backup_file = BACKUP_PATH + "PiFire_" + time_str + ".json"
    settings = read_settings()
    write_generic_json(settings, backup_file)
    # Save a path to the backup copy in the updater_manifest.json
    backup_manifest = read_generic_json("./backups/manifest.json")
    if backup_manifest == {}:
        backup_manifest = {"server_settings": {}}
        write_generic_json(backup_manifest, "./backups/manifest.json")

    server_version = settings["versions"]["server"]
    backup_manifest["server_settings"][server_version] = backup_file
    write_generic_json(backup_manifest, "backups/manifest.json")
    warning = f'Backed up your current settings to "{backup_file}" and setting these as the recovery settings for server version: {server_version}.'
    write_warning(warning)
    write_log(warning)
    return backup_file


def read_pellet_db_file(filename="pelletdb.json", retry_count=0):
    """
    Read Pellet DataBase from file

    :param filename: Filename to use (default pelletdb.json)
    :param retry_count: Recursion guard for the corrupt-file self-repair path
            below (mirrors read_settings_file's retry_count<5 pattern). The
            self-repair calls backup_pellet_db(action='restore'), which calls
            back into this function against the backup file -- if that backup is
            ALSO corrupt, this bounds the resulting recursion instead of letting
            it run away (RecursionError) when every backup on record is corrupt.
    """

    pelletdb = default_pellets()

    # Read all lines of pelletdb.json into a list(array)
    try:
        json_data_file = os.fdopen(os.open(filename, os.O_RDONLY))
        json_data_string = json_data_file.read()
        pelletdb_struct = json.loads(json_data_string)
        json_data_file.close()
    except IOError, OSError:
        # File not found, return default pellet database
        return pelletdb
    except:
        """ Restore PelletDB from backup if available """
        if retry_count < 5:
            pelletdb_struct = backup_pellet_db(action="restore", retry_count=retry_count + 1)
        else:
            # Backup is also corrupt/unreadable after repeated attempts --
            # stop recursing and fall back to defaults.
            return default_pellets()

    # Overlay the read values over the top of the default values
    #  This ensures that any NEW fields are captured.
    update_db = False  # set flag in case an update needs to be written back

    for key in pelletdb.keys():
        if key in pelletdb_struct.keys():
            pelletdb[key] = pelletdb_struct[key].copy()
        else:
            update_db = True

    return pelletdb


def backup_pellet_db(action="backup", retry_count=0):
    """Backup & Restore Pellet Database

    :param retry_count: Forwarded to read_pellet_db_file() on the 'restore'
            path, so repeated corrupt-backup self-repair recursion is bounded
            (see read_pellet_db_file).
    """
    backup_manifest = read_generic_json("./backups/manifest.json")
    if backup_manifest == {}:
        backup_manifest = {"server_settings": {}, "pelletdb": {"current": ""}}
        write_generic_json(backup_manifest, "./backups/manifest.json")

    if backup_manifest.get("pelletdb", None) == None:
        """ If the structure doesn't exist, create it. """
        backup_manifest["pelletdb"] = {"current": None}

    if action == "backup":
        time_now = datetime.datetime.now()
        time_str = time_now.strftime("%m-%d-%y_%H%M%S")  # Truncate the microseconds
        backup_file = BACKUP_PATH + "PelletDB_" + time_str + ".json"
        # Write the CURRENT pellet DB (SQLite is the source of truth at
        # runtime, pelletdb.json is not kept in sync) directly to the backup file.
        pelletdb = read_pellet_db()
        write_generic_json(pelletdb, backup_file)
        backup_manifest["pelletdb"]["current"] = backup_file
        message = f"Pellet DB has been backed up to the following file: {backup_file}"
        write_generic_json(backup_manifest, "./backups/manifest.json")
        write_log(message)
        return backup_file
    elif action == "restore":
        backup_pelletdb = backup_manifest["pelletdb"].get("current", None)
        if backup_pelletdb is not None:
            pelletdb_backup_file = backup_pelletdb
            warning = f"There was an issue with loading the Pellet Database (possibly corruption).  Restoring from the following backup file: {backup_pelletdb}."
            # Read the backup FILE (not SQLite -- that's the current,
            # possibly corrupt, state we're recovering from).
            pelletdb = read_pellet_db_file(filename=pelletdb_backup_file, retry_count=retry_count)
            write_pellet_db(pelletdb)
        else:
            warning = f"There was an issue with loading the Pellet Database (possibly corruption).  No backups found, setting to defaults."
            pelletdb = default_pellets()
            write_pellet_db(pelletdb)
        write_warning(warning)
        write_log(warning)
        return pelletdb
    else:
        pass

    return
