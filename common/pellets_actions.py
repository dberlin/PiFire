#!/usr/bin/env python3

"""Pellet-database actions, shared by the Socket.IO app-data channel and the
REST API.

These lived in blueprints/mobile/socket_io.py, which cannot be imported from
a Flask blueprint: it does `from app import socketio` at module scope and runs
seed_settings_store()/seed_pellets_store()/flush_connected_users() as import
side effects. This module imports neither Flask nor socketio.

CONTRACT (see docs/superpowers/plans/2026-07-25-react-pellets-page.md):
every handler takes an INTENT -- one action plus its arguments -- and does its
own read-modify-write of the pellet blob inside the request. write_pellet_db()
is a whole-blob set_blob() with no merge and no queue
(common/datastore_accessors.py:465-471, :702-703), and the control process
writes the same blob (controller/runtime/modes/base.py:374-375, :761-763), so
any caller that holds a database across a round trip and posts it back WILL
discard the controller's est_usage/hopper_level updates. Never add a handler
that accepts a whole pellet database.
"""

import time
from datetime import datetime

from common.app import api_response
from common.backups import backup_pellet_db
from common.common import WriteKind
from common.control_delta import control_delta
from common.datastore_accessors import write_control, write_pellet_db
from common.defaults import default_pellets


def _log_key(log):
    """A millisecond key for `log` that no existing entry already holds.

    Second resolution let two loads inside one second land on the same dict key
    and lose an entry; a millisecond that is already taken is advanced rather
    than overwritten, so the count is the number of loads whatever the clock
    does.
    """
    stamp = int(time.time() * 1000)
    while str(stamp) in log:
        stamp += 1
    return str(stamp)


def _validated_rating(action_data):
    """The rating as an int in 1..5, or None if the request did not carry one."""
    try:
        rating = int(action_data["rating"])
    except KeyError, TypeError, ValueError:
        return None
    return rating if 1 <= rating <= 5 else None


def clear_pellet_db():
    """Reset the pellet database to defaults -- the admin "Clear Pellet
    Database" action, shared by the admin page and the Socket.IO admin channel.

    Both transports used to run ``os.system("rm pelletdb.json")`` here. That
    worked when the pellet database WAS that file: deleting it made the next
    read_pellet_db_file() fall back to default_pellets(). SQLite is the store
    now (pelletdb.json only ever exists if someone runs
    scripts/export-pelletdb-json.py), so the rm removed nothing, the live blob
    was left untouched, and the action logged success while doing nothing.
    Reseeding the blob with default_pellets() is what that rm used to mean.
    """
    pelletdb = default_pellets()
    write_pellet_db(pelletdb)
    return pelletdb


def pellets_load_profile(pelletdb, action_data):
    if "profile" in action_data:
        pelletdb["current"]["pelletid"] = action_data["profile"]
        pelletdb["current"]["date_loaded"] = str(datetime.now())[0:19]
        pelletdb["current"]["est_usage"] = 0.0
        pelletdb["log"][_log_key(pelletdb["log"])] = {"pelletid": action_data["profile"], "deleted": False}
        # This handler changes one boolean, so one boolean is what it states.
        # Queuing the whole control dict would carry a stale snapshot of every
        # other member through the queue, and a delta says only what it means.
        write_control(control_delta(set_values={"hopper_check": True}), WriteKind.DELTA, origin="app")
        write_pellet_db(pelletdb)
        # Snapshot the new load, exactly as Flask's _pellets_loadprofile does
        # (blueprints/pellets/routes.py). The React "Load New Pellets" path
        # reaches this handler and previously left no restore point.
        backup_pellet_db(action="backup")
        return api_response(result="OK")
    else:
        return api_response(result="Error", message="Error: Profile not included in request")


def pellets_hopper_check(pelletdb, action_data):
    # MINIMAL patch -- see pellets_load_profile for the full rationale.
    write_control(control_delta(set_values={"hopper_check": True}), WriteKind.DELTA, origin="app")
    return api_response(result="OK")


def pellets_edit_brands(pelletdb, action_data):
    if "delete_brand" in action_data:
        delBrand = action_data["delete_brand"]
        if delBrand in pelletdb["brands"]:
            pelletdb["brands"].remove(delBrand)
        write_pellet_db(pelletdb)
        return api_response(result="OK")
    elif "new_brand" in action_data:
        newBrand = action_data["new_brand"]
        if newBrand not in pelletdb["brands"]:
            pelletdb["brands"].append(newBrand)
        write_pellet_db(pelletdb)
        return api_response(result="OK")
    else:
        return api_response(result="Error", message="Error: Function not specified")


def pellets_edit_woods(pelletdb, action_data):
    if "delete_wood" in action_data:
        delWood = action_data["delete_wood"]
        if delWood in pelletdb["woods"]:
            pelletdb["woods"].remove(delWood)
        write_pellet_db(pelletdb)
        return api_response(result="OK")
    elif "new_wood" in action_data:
        newWood = action_data["new_wood"]
        if newWood not in pelletdb["woods"]:
            pelletdb["woods"].append(newWood)
        write_pellet_db(pelletdb)
        return api_response(result="OK")
    else:
        return api_response(result="Error", message="Error: Function not specified")


def pellets_add_profile(pelletdb, action_data):
    rating = _validated_rating(action_data)
    if rating is None:
        return api_response(result="Error", message="Error: rating must be a whole number from 1 to 5")

    profile_id = "".join(filter(str.isalnum, str(datetime.now())))
    brand = action_data["brand_name"]
    wood = action_data["wood_type"]
    pelletdb["archive"][profile_id] = {
        "brand": brand,
        "wood": wood,
        "rating": rating,
        "comments": action_data["comments"],
    }
    # The vocabularies are autocomplete suggestions, and a bag they have not
    # heard of is the normal case -- so naming one adds it.
    if brand not in pelletdb["brands"]:
        pelletdb["brands"].append(brand)
    if wood not in pelletdb["woods"]:
        pelletdb["woods"].append(wood)

    if action_data["add_and_load"]:
        pelletdb["current"]["pelletid"] = profile_id
        # MINIMAL patch -- see pellets_load_profile for the full rationale.
        write_control(control_delta(set_values={"hopper_check": True}), WriteKind.DELTA, origin="app")
        pelletdb["current"]["date_loaded"] = str(datetime.now())[0:19]
        pelletdb["current"]["est_usage"] = 0.0
        pelletdb["log"][_log_key(pelletdb["log"])] = {"pelletid": profile_id, "deleted": False}

    write_pellet_db(pelletdb)
    return api_response(result="OK")


def pellets_edit_profile(pelletdb, action_data):
    if "profile" not in action_data:
        return api_response(result="Error", message="Error: Profile not included in request")
    rating = _validated_rating(action_data)
    if rating is None:
        return api_response(result="Error", message="Error: rating must be a whole number from 1 to 5")

    profile_id = action_data["profile"]
    brand = action_data["brand_name"]
    wood = action_data["wood_type"]
    pelletdb["archive"][profile_id]["brand"] = brand
    pelletdb["archive"][profile_id]["wood"] = wood
    pelletdb["archive"][profile_id]["rating"] = rating
    pelletdb["archive"][profile_id]["comments"] = action_data["comments"]
    if brand not in pelletdb["brands"]:
        pelletdb["brands"].append(brand)
    if wood not in pelletdb["woods"]:
        pelletdb["woods"].append(wood)
    write_pellet_db(pelletdb)
    return api_response(result="OK")


def pellets_delete_profile(pelletdb, action_data):
    if "profile" in action_data:
        profile_id = action_data["profile"]
        if pelletdb["current"]["pelletid"] == profile_id:
            return api_response(result="Error", message="Error: Cannot delete current profile")
        else:
            pelletdb["archive"].pop(profile_id)
            for index in pelletdb["log"]:
                if pelletdb["log"][index]["pelletid"] == profile_id:
                    pelletdb["log"][index] = {"pelletid": None, "deleted": True}
        write_pellet_db(pelletdb)
        return api_response(result="OK")
    else:
        return api_response(result="Error", message="Error: Profile not included in request")


def pellets_delete_log(pelletdb, action_data):
    if "log_item" in action_data:
        delLog = action_data["log_item"]
        if delLog in pelletdb["log"]:
            pelletdb["log"].pop(delLog)
        write_pellet_db(pelletdb)
        return api_response(result="OK")
    else:
        return api_response(result="Error", message="Error: Function not specified")


PELLETS_DISPATCH = {
    "load_profile": pellets_load_profile,
    "hopper_check": pellets_hopper_check,
    "edit_brands": pellets_edit_brands,
    "edit_woods": pellets_edit_woods,
    "add_profile": pellets_add_profile,
    "edit_profile": pellets_edit_profile,
    "delete_profile": pellets_delete_profile,
    "delete_log": pellets_delete_log,
}
