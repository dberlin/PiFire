import { useState } from "react";
import { Alert, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import type { CommandClient, CommandResult } from "@pifire/core/command";
import type { DashSocketPayload } from "@pifire/core/contracts/core";
import {
  type ControlButton,
  type MenuItem,
  buttonsForMode,
} from "@pifire/core/dashboard/buttonsForMode";
import { THEME } from "../theme";
import { SetpointModal } from "./SetpointModal";

/** The two ways out of a running cook. Ported from web-react's
 *  ControlButtons.tsx (`const SAFETY_LABELS = new Set(["Stop", "Shutdown"])`)
 *  -- never withheld by the row-wide `disabled` gate below, because a
 *  degraded live SOCKET does not mean the REST command endpoint they hit is
 *  unreachable too, and because these are the only way out of a running cook
 *  if it were. */
const SAFETY_LABELS = new Set(["Stop", "Shutdown"]);

const tokens = THEME.ember;

// Same lit-border-vs-fill idiom as web-react's VARIANT_STYLE (dashboard.css
// tokens re-expressed as flat RN colors, same reason GrillGauge.tsx hardcodes
// its palette: theme.ts carries no accent-aware gauge/button tokens yet).
const VARIANT_STYLE: Record<string, { borderColor: string; backgroundColor: string; color: string }> = {
  accent: { borderColor: tokens.accent, backgroundColor: "rgba(255,138,43,0.14)", color: tokens.accent },
  primary: { borderColor: tokens.accent, backgroundColor: tokens.accent, color: tokens.background },
  danger: { borderColor: tokens.danger, backgroundColor: "rgba(255,90,77,0.14)", color: tokens.danger },
  plain: { borderColor: "rgba(255,255,255,0.14)", backgroundColor: tokens.surface, color: tokens.text },
};

interface ControlRowProps {
  dash: DashSocketPayload;
  command: CommandClient;
  /** True while the live connection is not `"live"` (see useLive's `phase`).
   *  Disables every button except Stop/Shutdown -- see SAFETY_LABELS above. */
  disabled: boolean;
}

// Mode-driven control row. Renders exactly what buttonsForMode(dash) returns
// -- which buttons exist, their labels, their variants, and whether pressing
// one dispatches a command straight away or needs a confirmation first, is
// entirely that shared function's decision (buttonsForMode.ts's ButtonAction
// union: "command" never confirms, "confirm"/"startup" always do). This
// component only supplies the presentation for each action type, the same
// split web-react's ControlButtons.tsx makes.
export function ControlRow({ dash, command, disabled }: ControlRowProps) {
  const buttons = buttonsForMode(dash);
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
