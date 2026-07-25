// PiFire serves the wizard's vendor board photos from Flask's DEFAULT static
// folder -- `app = Flask(__name__)` (app.py:49) over the repo-root `static/`
// directory -- and the legacy card template builds exactly this URL:
// `url_for('static', filename='img/wizard/' + moduleData['image'])`
// (blueprints/wizard/templates/wizard/_macro_wizard_card.html:7).
// Verified live: GET http://localhost:5000/static/img/wizard/custom.png -> 200.
//
// wizard_manifest.json stores a BARE FILENAME ("pcb_4.x.x.png"), so using it
// directly as an <img src> resolves against the React app's own origin and
// 404s. These photos are how a user identifies which board they physically
// have, so a broken image is a loss of function, not a cosmetic defect.
//
// `baseUrl` is the same PUBLIC_PIFIRE_URL-derived value the wizard API client
// uses (helpers/wizard/wizardRoutes.ts:4): an absolute origin when the app is
// pointed at a remote PiFire (plain <img> loads need no CORS), or "" in the
// default dev setup, where rsbuild proxies /static/img through to Flask.
const WIZARD_IMAGE_PATH = "/static/img/wizard";

export function moduleImageUrl(baseUrl: string, image: string | undefined): string | undefined {
  if (!image) return undefined;
  return `${baseUrl}${WIZARD_IMAGE_PATH}/${image}`;
}
