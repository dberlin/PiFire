// How a routed page reads the app's live state.
//
// `useLiveState()` opens its own socket.io connection every time it is called,
// so exactly one component in the tree may call it: AppShell. Every page below
// the shell therefore takes the same subscription from Outlet context instead
// of subscribing again -- a second call would mean a second socket and a
// doubled `listen_app_data` stream. This is the same arrangement SettingsShell
// uses for the settings payload.

import { useOutletContext } from "react-router";

import type { LiveStateResult } from "./useLiveState";

/** The bundle AppShell puts on `<Outlet context={...}>`. */
export type ShellContext = LiveStateResult;

/** Read the shell's live state. Only valid inside a route nested in AppShell. */
export function useShellState(): ShellContext {
  return useOutletContext<ShellContext>();
}
