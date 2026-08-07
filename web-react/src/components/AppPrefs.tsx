import { createContext, type ReactNode, useContext, useEffect, useState } from "react";
import { readAccent } from "../helpers/settings/accent";
import { useSettings } from "../helpers/settings/useSettings";
import type { AccentName } from "../helpers/types";

interface AppPrefs {
  accent: AccentName;
  setAccent: (a: AccentName) => void;
  animate: boolean;
  setAnimate: (v: boolean) => void;
}
const Ctx = createContext<AppPrefs | null>(null);

export function AppPrefsProvider({ children }: { children: ReactNode }) {
  const [accent, setAccent] = useState<AccentName>("ember");
  const [animate, setAnimate] = useState(true);

  // Adopt the stored accent the FIRST time settings arrive, and never again:
  // after that the user's own click owns it, and any settings save invalidates
  // this key, so a later refetch must not reach back in and undo a swatch they
  // just picked.
  //
  // Render-phase adjustment, NOT a useEffect: the React Compiler is active and
  // `react-hooks/set-state-in-effect` rejects setState-in-effect. Same idiom
  // helpers/settings/settingsDrafts.ts:59 uses. It is legal here precisely
  // because `accent` is THIS component's own state -- which is why the seeding
  // moved out of DashboardRoute, where writing the provider's state during
  // render would instead be "update a component while rendering a different
  // component".
  const { data: settings } = useSettings();
  const [seeded, setSeeded] = useState(false);
  if (settings && !seeded) {
    setSeeded(true);
    setAccent(readAccent(settings));
  }

  useEffect(() => {
    document.documentElement.setAttribute("data-accent", accent);
  }, [accent]);
  return <Ctx.Provider value={{ accent, setAccent, animate, setAnimate }}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- pairs the provider with its hook, same module by design.
export function useAppPrefs(): AppPrefs {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAppPrefs must be used within AppPrefsProvider");
  return c;
}
