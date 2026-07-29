import { useEffect, useState } from "react";
import { Link } from "react-router";
import { fetchUpdateCheck } from "../../helpers/update/updateApi";
import type { UpdateCheck } from "../../helpers/update/updateTypes";

export function SystemUpdateCard() {
  const [check, setCheck] = useState<UpdateCheck | null>(null);

  useEffect(() => {
    void fetchUpdateCheck().then((r) => setCheck(r.ok ? r.data : null));
  }, []);

  const behindText =
    check === null
      ? "Update status unavailable"
      : check.behind > 0
        ? `${check.behind} commits behind`
        : "Up to date";

  return (
    <section className="pf-admin-card" aria-labelledby="admin-system-update">
      <h3 id="admin-system-update">System Update</h3>
      <p>Current version: {check?.current ?? "unknown"}</p>
      <p>{behindText}</p>
      <Link to="/update" className="pf-admin-btn">
        Open Updater
      </Link>
    </section>
  );
}
