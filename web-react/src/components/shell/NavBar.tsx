import { useState } from "react";
import { NavLink } from "react-router";
import "./shell.css";

// Ported from templates/base.html:63-82. All six destinations are shown so the
// new UI advertises the same surface as the Flask app, but only the three that
// have been ported are navigable. The other three render as disabled spans
// rather than links to the Flask pages -- linking out of the SPA would drop the
// live socket and strand the user in the old UI.
const NAV_ITEMS = [
  { label: "Dashboard", to: "/", end: true },
  { label: "Recipes", to: null, end: false },
  { label: "History", to: "/history", end: false },
  { label: "Events", to: null, end: false },
  { label: "Settings", to: "/settings", end: false },
  { label: "Admin", to: null, end: false },
] as const;

const NAV_LIST_ID = "pf-nav-list";

export function NavBar({ grillName }: { grillName: string }) {
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

      <ul id={NAV_LIST_ID} className={`pf-nav-list ${open ? "open" : ""}`}>
        {NAV_ITEMS.map((item) => (
          <li key={item.label} className="pf-nav-item">
            {item.to === null ? (
              <span
                className="pf-nav-link disabled"
                aria-disabled="true"
                title="Not available in the new interface yet"
              >
                {item.label}
              </span>
            ) : (
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) => `pf-nav-link ${isActive ? "active" : ""}`}
                onClick={() => setOpen(false)}
              >
                {item.label}
              </NavLink>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}
