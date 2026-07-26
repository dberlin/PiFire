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

from datetime import datetime

from common.app import api_response
from common.common import WriteKind
from common.control_delta import control_delta
from common.datastore_accessors import write_control, write_pellet_db


def pellets_load_profile(pelletdb, action_data):
    if "profile" in action_data:
        pelletdb["current"]["pelletid"] = action_data["profile"]
        now = str(datetime.now())
        now = now[0:19]
        pelletdb["current"]["date_loaded"] = now
        pelletdb["current"]["est_usage"] = 0
        pelletdb["log"][now] = action_data["profile"]
        # This handler changes one boolean, so one boolean is what it states.
        # Queuing the whole control dict would carry a stale snapshot of every
        # other member through the queue, and a delta says only what it means.
        write_control(control_delta(set_values={"hopper_check": True}), WriteKind.DELTA, origin="app")
        write_pellet_db(pelletdb)
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
    profile_id = "".join(filter(str.isalnum, str(datetime.now())))
    pelletdb["archive"][profile_id] = {
        "id": profile_id,
        "brand": action_data["brand_name"],
        "wood": action_data["wood_type"],
        "rating": action_data["rating"],
        "comments": action_data["comments"],
    }
    if action_data["add_and_load"]:
        pelletdb["current"]["pelletid"] = profile_id
        # MINIMAL patch -- see pellets_load_profile for the full rationale.
        write_control(control_delta(set_values={"hopper_check": True}), WriteKind.DELTA, origin="app")
        now = str(datetime.now())
        now = now[0:19]
        pelletdb["current"]["date_loaded"] = now
        pelletdb["current"]["est_usage"] = 0
        pelletdb["log"][now] = profile_id
        write_pellet_db(pelletdb)
        return api_response(result="OK")
    else:
        write_pellet_db(pelletdb)
        return api_response(result="OK")


def pellets_edit_profile(pelletdb, action_data):
    if "profile" in action_data:
        profile_id = action_data["profile"]
        pelletdb["archive"][profile_id]["brand"] = action_data["brand_name"]
        pelletdb["archive"][profile_id]["wood"] = action_data["wood_type"]
        pelletdb["archive"][profile_id]["rating"] = action_data["rating"]
        pelletdb["archive"][profile_id]["comments"] = action_data["comments"]
        write_pellet_db(pelletdb)
        return api_response(result="OK")
    else:
        return api_response(result="Error", message="Error: Profile not included in request")


def pellets_delete_profile(pelletdb, action_data):
    if "profile" in action_data:
        profile_id = action_data["profile"]
        if pelletdb["current"]["pelletid"] == profile_id:
            return api_response(result="Error", message="Error: Cannot delete current profile")
        else:
            pelletdb["archive"].pop(profile_id)
            for index in pelletdb["log"]:
                if pelletdb["log"][index] == profile_id:
                    pelletdb["log"][index] = "deleted"
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
