/** Every line the installer logs carries the wizard logger's prefix:
 *
 *     2026-08-01 12:00:03 +0000 | INFO | Resolved 12 packages
 *
 * which is 40-odd characters of furniture in a panel that is one column wide on
 * a phone -- the width where a first-time setup is most likely to be running.
 * The clock is the one part worth keeping: watching the timestamps stop moving
 * is how you tell a slow build from a hung one, which is the whole reason to
 * open the panel. The date and level go.
 *
 * A line that does not match is passed through untouched rather than dropped,
 * so anything the logger did not write -- a multi-line traceback, output from a
 * command that prints its own timestamps -- still reaches the screen intact.
 */
const LOGGER_PREFIX = /^\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2})[^|]*\|\s*\w+\s*\|\s?/;

export function formatInstallLog(text: string): string {
  return text
    .split("\n")
    .map((line) => line.replace(LOGGER_PREFIX, "$1  "))
    .join("\n");
}
