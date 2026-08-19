import { AppState } from "react-native";
import { act, renderHook } from "@testing-library/react-native";
import { useLive } from "../src/useLive";

jest.mock("@pifire/core/liveConnection", () => ({
  createLiveConnection: jest.fn(() => ({ reconnect: jest.fn(), close: jest.fn() })),
}));

it("reconnects when the app returns to the foreground", async () => {
  const { createLiveConnection } = require("@pifire/core/liveConnection");

  // react-native@0.86's real AppState wraps a native module through
  // NativeEventEmitter and exposes only addEventListener/
  // removeEventListener -- there is no `.emit` a test can drive directly,
  // and replacing the whole "react-native" module forces eager evaluation
  // of unrelated native modules (DevMenu, Clipboard) that aren't mocked in
  // this environment. Instead, capture the handler useLive registers and
  // invoke it directly to simulate an OS-driven foreground/background
  // transition.
  let handler: ((state: string) => void) | undefined;
  jest.spyOn(AppState, "addEventListener").mockImplementation((_type, h) => {
    handler = h as (state: string) => void;
    return { remove: () => {} } as ReturnType<typeof AppState.addEventListener>;
  });

  // @testing-library/react-native@14's renderHook is async; it must be
  // awaited before the effect that creates the connection has run.
  await renderHook(() => useLive("http://pifire.local:5000"));
  const connection = createLiveConnection.mock.results[0].value;

  act(() => {
    handler?.("background");
    handler?.("active");
  });

  expect(connection.reconnect).toHaveBeenCalledTimes(1);
});
