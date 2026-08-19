/**
 * Fails a test that logs an unexpected `console.error` or `console.warn`.
 *
 * A React `act()` warning, a duplicate-key warning, a react-query "data cannot
 * be undefined" error: each one is a defect the assertions in a test are
 * structurally unable to see, because the component still renders something and
 * the test still passes. Routing them through a per-test guard makes the console
 * an assertion surface like any other.
 *
 * A test that MEANS to log -- one driving a fail-open branch whose whole
 * contract is "warn and carry on" -- declares it with `expectConsole()`, which
 * both permits the message and requires it: a silenced warning is as much a
 * regression as a new one.
 *
 * State lives on `globalThis` rather than in module scope because the setup file
 * and the test files importing `expectConsole` are separate module graphs; a
 * module-level binding would be a different object in each.
 */

type Level = "error" | "warn";

interface Expectation {
  level: Level;
  match: string | RegExp;
  seen: number;
}

interface GuardState {
  expectations: Expectation[];
  unexpected: string[];
}

const STATE_KEY = "__pfConsoleGuard";

// Captured once, at import: install() runs per test and would otherwise wrap
// its own wrapper.
const REAL: Record<Level, (...args: unknown[]) => void> = {
  error: console.error.bind(console),
  warn: console.warn.bind(console),
};

function state(): GuardState {
  const host = globalThis as { [STATE_KEY]?: GuardState };
  if (!host[STATE_KEY]) host[STATE_KEY] = { expectations: [], unexpected: [] };
  return host[STATE_KEY];
}

function textOf(args: unknown[]): string {
  return args
    .map((arg) => {
      if (arg instanceof Error) return String(arg);
      if (typeof arg === "object" && arg !== null) {
        try {
          return JSON.stringify(arg);
        } catch {
          return String(arg);
        }
      }
      return String(arg);
    })
    .join(" ");
}

function matches(match: string | RegExp, text: string): boolean {
  return typeof match === "string" ? text.includes(match) : match.test(text);
}

/**
 * Permit -- and require -- one console message in the current test.
 *
 * `match` is a substring or a RegExp tested against the formatted arguments,
 * so `console.warn("Wizard: failed", err)` matches /failed.*network/.
 */
export function expectConsole(level: Level, match: string | RegExp): void {
  state().expectations.push({ level, match, seen: 0 });
}

export function installConsoleGuard(): void {
  const guard = state();
  guard.expectations = [];
  guard.unexpected = [];
  for (const level of ["error", "warn"] as const) {
    console[level] = (...args: unknown[]) => {
      const text = textOf(args);
      const expected = guard.expectations.find((e) => e.level === level && matches(e.match, text));
      if (expected) {
        expected.seen += 1;
        return;
      }
      guard.unexpected.push(`console.${level}: ${text}`);
      // Still print it: the failure message names the message, the real console
      // carries the stack that says where it came from.
      REAL[level](...args);
    };
  }
}

export function assertConsoleClean(): void {
  const guard = state();
  const problems = [
    ...guard.expectations
      .filter((e) => e.seen === 0)
      .map((e) => `expected a console.${e.level} matching ${String(e.match)}, none was logged`),
    ...guard.unexpected,
  ];
  guard.expectations = [];
  guard.unexpected = [];
  console.error = REAL.error;
  console.warn = REAL.warn;
  if (problems.length > 0) {
    throw new Error(
      `Unexpected console output during this test:\n  ${problems.join("\n  ")}\n` +
        "Fix the cause, or declare it with expectConsole() from tests/consoleGuard.ts.",
    );
  }
}
