import { useState } from "react";
import { NavLink } from "react-router";
import "./shell.css";

// Ported from templates/base.html:63-82. All six Flask destinations are shown
// so the new UI advertises the same surface as the Flask app, and every one of
// them now has a React route -- Events was the last that did not. Nothing here
// links out to a Flask page: that would drop the live socket and strand the
// user in the old UI.
//
// The `to: null` case and the disabled span it rendered are gone with it. They
// existed to advertise a destination the SPA could not yet reach, and there is
// no such destination left; TypeScript narrowed the branch to `never` the
// moment Events got its route.
//
// "Pellets" is the one entry with NO counterpart in base.html: Flask reaches
// the pellet manager only from the dashboard hopper card's "Manager" button
// (_macro_dash_default.html:360), not from the navbar. React has both -- this
// entry, and the hopper card's own shortcut (HopperGauge.tsx, ruled 2026-07-26)
// -- which is a deliberate superset, not a duplicate to be pruned: the navbar
// reaches the manager from every page, the card reaches it from the reading
// that prompts the visit.
const NAV_ITEMS = [
  { label: "Dashboard", to: "/", end: true },
  { label: "Recipes", to: "/recipes", end: false },
  { label: "History", to: "/history", end: false },
  { label: "Pellets", to: "/pellets", end: false },
  { label: "Events", to: "/events", end: false },
  { label: "Settings", to: "/settings", end: false },
  { label: "Admin", to: "/admin", end: false },
] as const;

const NAV_LIST_ID = "pf-nav-list";

// The stopwatch face beside the hamburger, ported from base.html:50-57 (a
// fa-stopwatch button wired to timerToggle()). Drawn inline because the React
// app carries no icon font.
function StopwatchIcon() {
  return (
    <svg width={16} height={17} viewBox="0 0 20 21" aria-hidden="true" focusable="false">
      <g fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
        <path d="M8 1.5h4" />
        <path d="M10 1.5v2.2" />
        <circle cx={10} cy={12.5} r={7} />
        <path d="M10 8.8v3.7h2.6" />
      </g>
    </svg>
  );
}

export function NavBar({
  grillName,
  timerVisible,
  timerRunning,
  onToggleTimer,
}: {
  grillName: string;
  /** Whether the shell is currently showing the timer bar. */
  timerVisible: boolean;
  /** Whether a timer is counting down right now. */
  timerRunning: boolean;
  /** Show/hide the timer bar. */
  onToggleTimer: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <nav className="pf-nav" aria-label="Main">
      <NavLink to="/" className="pf-nav-brand" onClick={() => setOpen(false)}>
        <span className="pf-nav-mark" aria-hidden="true" />
        <b>
          Pi<i className="pf-nav-fire">Fire</i>
        </b>
        {grillName ? <small className="pf-nav-grill">{grillName}</small> : null}
      </NavLink>

      <div className="pf-nav-actions">
        {/* base.html:50-57 keeps the stopwatch outside the collapsible menu,
            so the timer stays one click away on a phone. `aria-pressed` is
            what carries the on/off state; the accessible name stays fixed so
            the control does not rename itself under the user. The running
            marker mirrors timer.js:207 / :180, which recolours this same
            button while a timer counts down and clears it when it ends. */}
        <button
          type="button"
          className={`pf-nav-timer ${timerVisible ? "on" : ""} ${timerRunning ? "running" : ""}`}
          aria-label="Toggle timer bar"
          aria-pressed={timerVisible}
          title={timerRunning ? "Timer running" : "Timer"}
          onClick={onToggleTimer}
        >
          <StopwatchIcon />
        </button>

        <button
          type="button"
          className="pf-nav-toggle"
          aria-label="Toggle navigation"
          aria-expanded={open}
          aria-controls={NAV_LIST_ID}
          onClick={() => setOpen((o) => !o)}
        >
          <span className="pf-nav-bars" aria-hidden="true" />
        </button>
      </div>

      <ul id={NAV_LIST_ID} className={`pf-nav-list ${open ? "open" : ""}`}>
        {NAV_ITEMS.map((item) => (
          <li key={item.label} className="pf-nav-item">
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) => `pf-nav-link ${isActive ? "active" : ""}`}
              onClick={() => setOpen(false)}
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
