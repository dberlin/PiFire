import { createContext, type ReactNode, useContext, useEffect, useState } from "react";
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
