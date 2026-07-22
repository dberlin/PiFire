import type { ReactNode } from "react";
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="pf-section">
      <h2 className="pf-section-title">{title}</h2>
      <div className="pf-section-body">{children}</div>
    </section>
  );
}
