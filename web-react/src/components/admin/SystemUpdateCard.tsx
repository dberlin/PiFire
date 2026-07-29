import { useEffect, useState } from "react";
import { Link } from "react-router";
import { behindText } from "../../helpers/update/behindText";
import { fetchUpdateCheck } from "../../helpers/update/updateApi";
import type { UpdateCheck } from "../../helpers/update/updateTypes";

export function SystemUpdateCard() {
  const [check, setCheck] = useState<UpdateCheck | null>(null);

  useEffect(() => {
    void fetchUpdateCheck().then((r) => setCheck(r.ok ? r.data : null));
  }, []);

  return (
    <section className="pf-admin-card" aria-labelledby="admin-system-update">
      <h2 className="pf-admin-card-title" id="admin-system-update">
        System Update
      </h2>
      <p>Current version: {check?.current ?? "unknown"}</p>
      <p>{behindText(check?.behind ?? null)}</p>
      <Link to="/update" className="pf-admin-btn">
        Open Updater
      </Link>
    </section>
  );
}
