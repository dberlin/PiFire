import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";

/** A value that terminates a path rather than being descended into. Arrays are
 *  terminal: the tabs replace a whole list (temp_range_list, profiles) rather
 *  than addressing into one. */
type Leaf = string | number | boolean | null | undefined | readonly unknown[];

/** Every dotted path the settings tree admits, to any depth. */
export type PathsOf<T> = T extends Leaf
  ? never
  : {
      [K in keyof T & string]-?: NonNullable<T[K]> extends Leaf
        ? K
        : K | `${K}.${PathsOf<NonNullable<T[K]>>}`;
    }[keyof T & string];

/** The type stored at a dotted path. */
export type ValueAt<T, P extends string> = P extends `${infer Head}.${infer Rest}`
  ? Head extends keyof T
    ? ValueAt<NonNullable<T[Head]>, Rest>
    : never
  : P extends keyof T
    ? T[P]
    : never;

export type SettingsPath = PathsOf<SettingsSchema>;
