import type { LogFamily } from "@pifire/core/contracts/operations";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { fetchLogFamilies } from "../../helpers/logs/logsApi";
import { LogFilesTab } from "./LogFilesTab";
import { LogViewer } from "./LogViewer";

import "./logs.css";

const EVENTS_STEM = "events";
const TABS = [
  { id: "events", label: "Events" },
  { id: "files", label: "Log Files" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/**
 * The event feed and the log-file browser, one page.
 *
 * Both tabs are the same viewer over a different family, which is why they live
 * together rather than as two routes that would quietly diverge. Flask has them
 * as separate pages (/events and /logs) and they have diverged accordingly --
 * only one of them can page through a rotated file.
 *
 * The active tab lives in the query string so a reload, a bookmark or a shared
 * link lands back on the tab it named.
 */
export function EventsPage() {
  const [params, setParams] = useSearchParams();
  const active: TabId = params.get("tab") === "files" ? "files" : "events";
  const [families, setFamilies] = useState<LogFamily[]>([]);
  const [follow, setFollow] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchLogFamilies().then((found) => {
      if (!cancelled) setFamilies(found);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="pf-log-page">
      <h1 className="pf-log-title">Events</h1>

      <div className="pf-log-tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            className="pf-log-tab"
            aria-selected={active === tab.id}
            onClick={() => setParams(tab.id === "events" ? {} : { tab: tab.id })}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {active === "events" ? (
        <>
          <div className="pf-log-controls">
            <label className="pf-field">
              <input
                type="checkbox"
                checked={follow}
                onChange={(e) => setFollow(e.target.checked)}
              />
              <span className="pf-field-label">Follow new events</span>
            </label>
          </div>
          <LogViewer stem={EVENTS_STEM} follow={follow} />
        </>
      ) : (
        //  Carries its own follow control: a family's current file is live,
        //  and which one is selected is that tab's own state.
        <LogFilesTab families={families} />
      )}
    </section>
  );
}
