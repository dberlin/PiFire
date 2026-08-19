import { useState } from "react";
import { Alert, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import type { CommandClient, CommandResult } from "@pifire/core/command";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import {
  type ControlButton,
  type MenuItem,
  buttonsForMode,
} from "@pifire/core/dashboard/buttonsForMode";
import { BODY_TEXT_COLOR, INSET_COLOR, ON_ACCENT_INK, THEME, withAlpha, type AccentName } from "../theme";
import { SetpointModal } from "./SetpointModal";

// text/surface/danger are identical across all three accents (see theme.ts's
// own note on THEME) so the menu modal below -- which has no accent-specific
// styling on the web either -- can read them off any one entry.
const tokens = THEME.ember;

/** The two ways out of a running cook. Ported from web-react's
 *  ControlButtons.tsx (`const SAFETY_LABELS = new Set(["Stop", "Shutdown"])`)
 *  -- never withheld by the row-wide `disabled` gate below, because a
 *  degraded live SOCKET does not mean the REST command endpoint they hit is
 *  unreachable too, and because these are the only way out of a running cook
 *  if it were. */
const SAFETY_LABELS = new Set(["Stop", "Shutdown"]);

// Ported literally from web-react's ControlButtons.tsx VARIANT_STYLE
// (dashboard.css has no rule for this -- the colors are inline styles there
// too): primary fills solid with the accent, accent lights a border over a
// 16%-alpha accent tint, danger the same at 14% with var(--danger), and plain
// sits on the inset surface. `accent` is passed in rather than fixed to ember
// so a button matches whichever accent the user picked, same as var(--accent)
// tracking [data-accent] on the web.
function variantStyle(accent: AccentName) {
  const accentColor = THEME[accent].accent;
  return {
    accent: { borderColor: accentColor, backgroundColor: withAlpha(accentColor, 0.16), color: BODY_TEXT_COLOR },
    primary: { borderColor: "transparent", backgroundColor: accentColor, color: ON_ACCENT_INK },
    danger: {
      borderColor: THEME[accent].danger,
      backgroundColor: withAlpha(THEME[accent].danger, 0.14),
      color: THEME[accent].danger,
    },
    plain: { borderColor: "rgba(255,255,255,0.14)", backgroundColor: INSET_COLOR, color: BODY_TEXT_COLOR },
  } as const;
}

interface ControlRowProps {
  dash: DashSocketPayload;
  command: CommandClient;
  /** True while the live connection is not `"live"` (see useLive's `phase`).
   *  Disables every button except Stop/Shutdown -- see SAFETY_LABELS above. */
  disabled: boolean;
  /** Which accent's colors light the primary/accent button variants. Optional
   *  and defaulting to "ember" so callers (and existing tests) that don't
   *  care about accent don't have to thread one through. */
  accent?: AccentName;
}

// Mode-driven control row. Renders exactly what buttonsForMode(dash) returns
// -- which buttons exist, their labels, their variants, and whether pressing
// one dispatches a command straight away or needs a confirmation first, is
// entirely that shared function's decision (buttonsForMode.ts's ButtonAction
// union: "command" never confirms, "confirm"/"startup" always do). This
// component only supplies the presentation for each action type, the same
// split web-react's ControlButtons.tsx makes.
export function ControlRow({ dash, command, disabled, accent = "ember" }: ControlRowProps) {
  const buttons = buttonsForMode(dash);
  const VARIANT_STYLE = variantStyle(accent);
  const [setpointOpen, setSetpointOpen] = useState(false);
  const [menu, setMenu] = useState<{
    title: string;
    items: MenuItem[];
    run(c: CommandClient, value: string): Promise<CommandResult>;
  } | null>(null);

  const fire = async (run: (c: CommandClient) => Promise<CommandResult>) => {
    try {
      const res = await run(command);
      if (!res.ok) {
        Alert.alert("Command failed", res.message || "the grill did not accept that command");
      }
    } catch (e) {
      Alert.alert("Command failed", e instanceof Error ? e.message : String(e));
    }
  };

  const onPress = (button: ControlButton) => {
    const { action } = button;
    if (action.type === "command") {
      void fire(action.run);
    } else if (action.type === "confirm") {
      Alert.alert(action.title, undefined, [
        { text: "Cancel", style: "cancel" },
        { text: "Confirm", style: "destructive", onPress: () => void fire(action.run) },
      ]);
    } else if (action.type === "startup") {
      // ONE variant here (unlike web's two-variant #startupModal): the
      // hold-goto variant needs a settings write (start_to_mode.primary_setpoint)
      // this app has no REST settings client for yet. The plain safety-check
      // confirm below is what startupCheck: true, startToHoldPrompt: false
      // resolves to.
      Alert.alert("Startup Check", "Confirm Startup Grill?", [
        { text: "Cancel", style: "cancel" },
        { text: "Startup", onPress: () => void fire((c) => c.setMode("startup")) },
      ]);
    } else if (action.type === "setpoint") {
      setSetpointOpen(true);
    } else if (action.type === "menu") {
      setMenu({ title: action.title, items: action.items, run: action.run });
    } else if (action.type === "pwm") {
      // No PwmEntry equivalent shipped on mobile yet; degrade gracefully
      // rather than silently doing nothing.
      Alert.alert("Not available", "Fan speed control isn't available on mobile yet.");
    }
  };

  return (
    <View style={styles.row}>
      {buttons.map((button) => {
        const isSafety = SAFETY_LABELS.has(button.label);
        const isDisabled = button.disabled === true || (!isSafety && disabled);
        const variant = VARIANT_STYLE[button.variant ?? "plain"] ?? VARIANT_STYLE.plain;
        return (
          <Pressable
            key={button.label}
            disabled={isDisabled}
            onPress={() => onPress(button)}
            style={[
              styles.button,
              { borderColor: variant.borderColor, backgroundColor: variant.backgroundColor },
              isDisabled ? styles.buttonDisabled : null,
            ]}
          >
            <Text style={[styles.buttonText, { color: variant.color }]}>{button.label}</Text>
          </Pressable>
        );
      })}

      <SetpointModal
        open={setpointOpen}
        // Same fallback chain web's ControlButtons.tsx uses: a real setpoint,
        // else the live pit reading, else 0 for a probe with nothing at all.
        initial={dash.primaryProbe.setTemp || dash.primaryProbe.temp || 0}
        units={dash.tempUnits}
        safetyMaxTemp={dash.safetyMaxTemp}
        onCancel={() => setSetpointOpen(false)}
        onSubmit={(tempF) => {
          setSetpointOpen(false);
          void fire((c) => c.hold(tempF));
        }}
      />

      <Modal
        visible={menu !== null}
        transparent
        animationType="fade"
        onRequestClose={() => setMenu(null)}
      >
        <Pressable style={styles.scrim} onPress={() => setMenu(null)}>
          <Pressable style={styles.menu} onPress={(e) => e.stopPropagation()}>
            <Text style={styles.menuTitle}>{menu?.title ?? ""}</Text>
            {(menu?.items ?? []).map((item) => (
              <Pressable
                key={item.value}
                style={styles.menuItem}
                onPress={() => {
                  const run = menu?.run;
                  setMenu(null);
                  if (run !== undefined) void fire((c) => run(c, item.value));
                }}
              >
                <Text style={styles.menuItemText}>{item.label}</Text>
              </Pressable>
            ))}
            <Pressable style={styles.menuCancel} onPress={() => setMenu(null)}>
              <Text style={styles.menuCancelText}>Cancel</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  button: {
    borderWidth: 1,
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 14,
    minWidth: 84,
    alignItems: "center",
    justifyContent: "center",
  },
  buttonDisabled: {
    opacity: 0.4,
  },
  buttonText: {
    fontSize: 14,
    fontWeight: "600",
  },
  scrim: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.6)",
    alignItems: "center",
    justifyContent: "center",
  },
  menu: {
    backgroundColor: tokens.surface,
    borderRadius: 16,
    padding: 16,
    gap: 4,
    width: "80%",
  },
  menuTitle: {
    color: tokens.text,
    fontSize: 16,
    fontWeight: "600",
    marginBottom: 8,
    textAlign: "center",
  },
  menuItem: {
    paddingVertical: 12,
  },
  menuItemText: {
    color: tokens.text,
    fontSize: 15,
    textAlign: "center",
  },
  menuCancel: {
    paddingVertical: 12,
    marginTop: 4,
  },
  menuCancelText: {
    color: tokens.danger,
    fontSize: 15,
    textAlign: "center",
    fontWeight: "600",
  },
});
