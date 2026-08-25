"""
*****************************************
 PiFire Experiment Guard: pinned to the two-lump grey box
*****************************************

 Importing this module refuses to run an experiment that was written against
 the TWO-LUMP grey box (a firepot T_f coupled to the chamber T_c through h_fc)
 now that controller/mpc_model.py implements a single chamber lump.

 The experiments that import it are records of finished questions. Their
 numbers describe a model this repo no longer has, and porting them forward
 would change what they measured -- which is the opposite of what a record is
 for. So they are kept as written and stopped at the door rather than left to
 produce an answer that looks current.

 The alternative was worse in both directions. Several of these still crash on
 their own now (`_DEFAULTS["h_fc"]` raises KeyError, `simulate_grey_box(C_f=)`
 raises TypeError), but the traceback says nothing about why; and a few build
 their own model end-to-end and would run to completion, comparing a two-lump
 experiment against a single-lump production controller and printing a number
 that is simply wrong with no indication of it.

 Usage, as the LAST import in a pinned experiment:

     import _pinned_two_lump  # noqa: F401  (refuses to run: see the module)

*****************************************
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from controller.mpc_model import MODEL_SCHEMA  # noqa: E402

#: The structure these experiments were written against: schema 1, two lumps.
PINNED_SCHEMA = 1

_MESSAGE = """
{name} is pinned to grey-box model schema {pinned} -- the TWO-LUMP model, a
firepot T_f coupled to the chamber through h_fc. controller/mpc_model.py now
implements schema {live}, a single chamber lump, so this experiment's numbers
describe a model this repo no longer has.

It is kept as a record of a finished question, not re-run. Porting it forward
would change what it measured. If you need this measurement against the
current model, write a new experiment -- see ndelay_sweep.py, which carries
BOTH structures explicitly and verifies its twin against the shipped simulator
before using it.
"""


def require_pinned_model(name=None):
    """Stop unless the shipped model is still the one this was written for."""
    if MODEL_SCHEMA != PINNED_SCHEMA:
        raise SystemExit(_MESSAGE.format(name=name or "This experiment", pinned=PINNED_SCHEMA, live=MODEL_SCHEMA))


require_pinned_model()
