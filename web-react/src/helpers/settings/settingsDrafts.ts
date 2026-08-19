import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { type Dispatch, type SetStateAction, useCallback, useState } from "react";
import { useOutletContext } from "react-router";
import type { EditableSettingsTabId } from "./settingsTabs";

/**
 * Where a settings tab's in-progress edit lives while the user is somewhere
 * else.
 *
 * A tab component unmounts the moment the user clicks another pill, so state
 * held with `useState` inside the tab dies with it -- which is how a
 * half-finished SmartStart or PWM table edit used to disappear, silently, on
 * navigation. Flask had no such problem because it persisted on change; React's
 * per-tab SaveBar is the deliberate replacement, so the edit has to be HELD
 * until that Save instead (ruling 3, 2026-07-26,
 * docs/superpowers/backlogs/react-migration-backlog.md).
 *
 * The store therefore lives on SettingsShell, which stays mounted for the whole
 * visit, and is handed to tabs through the same Outlet context that already
 * carries `settings`. Leaving /settings entirely unmounts the shell and drops
 * every draft with it, which is the intended scope: this survives a tab switch,
 * not a page reload.
 */
export type SettingsDraft = {
  value: unknown;
  /**
   * True once this tab's own Save came back OK. The draft is NOT deleted at
   * that moment -- the loader has not caught up yet, so falling back to
   * `settings` would flash the pre-save values back on screen -- it stops
   * counting as unsaved and is dropped when the next loader result arrives.
   */
  saved: boolean;
};

export type SettingsDrafts = Partial<Record<EditableSettingsTabId, SettingsDraft>>;

export type SettingsDraftStore = {
  drafts: SettingsDrafts;
  setDrafts: Dispatch<SetStateAction<SettingsDrafts>>;
};

/** The Outlet context every settings tab reads. */
export type SettingsDraftContext = { settings: SettingsSchema } & SettingsDraftStore;

/**
 * Owns the draft map. Call it in the component that outlives the tabs
 * (SettingsShell, or the test harness standing in for it) and spread the result
 * into the Outlet context.
 *
 * Fresh loader data retires the drafts that have already been saved and ONLY
 * those: a revalidation can be triggered by another tab's save, by a sibling
 * loader or by a poll, and dropping an untouched-by-that in-progress edit would
 * be the same silent loss this exists to prevent.
 */
export function useSettingsDraftStore(settings: unknown): SettingsDraftStore {
  const [drafts, setDrafts] = useState<SettingsDrafts>({});
  // Render-phase adjustment, NOT a useEffect: the React Compiler is active and
  // `react-hooks/set-state-in-effect` rejects setState-in-effect. Same idiom the
  // tabs used before they handed their state to this store.
  const [prevSettings, setPrevSettings] = useState(settings);
  if (settings !== prevSettings) {
    setPrevSettings(settings);
    setDrafts((d) => {
      const kept = Object.entries(d).filter(([, entry]) => !entry.saved);
      return kept.length === Object.keys(d).length ? d : Object.fromEntries(kept);
    });
  }
  return { drafts, setDrafts };
}

/**
 * One tab's slice of the store, with the same shape a `useState` pair had.
 *
 * `key` is the tab's route path, which is what lets SettingsShell mark the
 * matching nav pill. `read` derives the tab's values from `settings` and is
 * used whenever there is no draft -- so an untouched tab always shows what the
 * server last returned, with no copy of it kept anywhere.
 */
export function useSettingsDraft<T>(
  key: EditableSettingsTabId,
  read: (settings: SettingsSchema) => T,
) {
  const { settings, drafts, setDrafts } = useOutletContext<SettingsDraftContext>();
  const entry = drafts[key];
  const value = entry ? (entry.value as T) : read(settings);

  const setValue = useCallback(
    (update: SetStateAction<T>) => {
      setDrafts((d) => {
        const current = d[key] ? (d[key].value as T) : read(settings);
        const next = typeof update === "function" ? (update as (prev: T) => T)(current) : update;
        return { ...d, [key]: { value: next, saved: false } };
      });
    },
    [key, read, settings, setDrafts],
  );

  const markSaved = useCallback(() => {
    setDrafts((d) => (d[key] ? { ...d, [key]: { ...d[key], saved: true } } : d));
  }, [key, setDrafts]);

  /** Throw the draft away and go back to what the loader last returned. For a
   *  tab that offers an explicit Discard; a save uses markSaved() instead. */
  const clear = useCallback(() => {
    setDrafts((d) => {
      if (!d[key]) return d;
      const { [key]: _dropped, ...rest } = d;
      return rest;
    });
  }, [key, setDrafts]);

  return { value, setValue, dirty: !!entry && !entry.saved, markSaved, clear };
}
