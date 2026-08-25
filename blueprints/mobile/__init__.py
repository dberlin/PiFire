"""Socket.IO handlers for the live dashboard feed.

`socket_io` is imported for its side effects by app.py, once the `socketio`
object it depends on exists. This package deliberately imports nothing itself:
doing so here would run socket_io at `blueprints.mobile` import time, which is
earlier than app.py can guarantee.
"""
