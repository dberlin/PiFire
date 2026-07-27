"""Recipe endpoint handlers for /api/files/recipes/*.

Every handler here takes a BARE FILENAME resolved by routes.require_file
against routes.recipe_folder(). None of them ever accepts a path, which is the
single behavioural difference from blueprints/recipes/routes.py.

comments.json is deliberately absent from every payload: no UI in either app has
ever written a recipe comment (tests/web/test_page_recipes.py:33-38). The member
is preserved on every write because update_json_file_data rewrites one member
and copies the rest.
"""

from common.app import api_response, classify_cookfile_error
from file_mgmt.recipes import read_recipefile


def load(path):
    """read_recipefile, with the (struct, status) shape the routes branch on."""
    return read_recipefile(path)


def unreadable(status, error):
    """422 for an archive that exists but will not open."""
    return error("unreadable", 422, errortype=classify_cookfile_error(status))


def detail_payload(struct, filename):
    """Everything the React editor needs, and nothing it does not."""
    return {
        "filename": filename,
        "metadata": struct["metadata"],
        "recipe": struct["recipe"],
        "assets": struct["assets"],
    }
