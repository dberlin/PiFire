import { useEffect, useState } from "react";
import { Modal, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { setpointRange } from "@pifire/core/dashboard/health";
import { THEME } from "../theme";

// This modal is mobile's ONE setpoint entry, opened by the plain "Hold"
// button (buttonsForMode's {type: "setpoint"} action) -- the equivalent of
// web's ORDINARY SetpointEntry instance, not its startup-hold-prompt
// variant. Web's ordinary instance passes no `min` override to SetpointEntry,
// so it falls through to setpointRange()'s own floor (health.ts's
// SETPOINT_MIN, 150°F/65°C) -- that is the floor this modal matches, both
// bounds from the same shared function. (An earlier version of this file
// used web's HOLD_PROMPT_MIN, 125°F/50°C, here -- that constant is real, but
// it belongs to web's *startup*-hold-prompt modal specifically, a variant
// mobile does not implement yet; see ControlRow.tsx's note on the
// single-variant startup confirm.)
interface SetpointModalProps {
  open: boolean;
  /** Prefill, e.g. `dash.primaryProbe.setTemp || dash.primaryProbe.temp || 0`. */
  initial: number;
  units: "F" | "C";
  /** dash.safetyMaxTemp -- the grill's own shutdown limit. The ceiling comes
   *  from here, never a hardcoded number (setpointRange falls back to a fixed
   *  ceiling only when this is absent or nonsensical). */
  safetyMaxTemp: number | undefined;
  onCancel(): void;
  onSubmit(tempF: number): void;
}

const tokens = THEME.ember;

export function SetpointModal({
  open,
  initial,
  units,
  safetyMaxTemp,
  onCancel,
  onSubmit,
}: SetpointModalProps) {
  // Both bounds from the same shared function: floor is SETPOINT_MIN
  // (health.ts), ceiling is the grill's own shutdown limit when present and
  // sensible, else health.ts's fixed fallback -- exactly what web's ordinary
  // (no `min` override) SetpointEntry instance enforces.
  const { min: lo, max: hi } = setpointRange(units, safetyMaxTemp);
  const clamp = (t: number) => {
    const r = Math.round(t);
    return r < lo ? lo : r > hi ? hi : r;
  };
  const step = units === "F" ? 5 : 3;

  const [temp, setTemp] = useState(() => clamp(initial));
  const [draft, setDraft] = useState<string | null>(null);

  // Re-seed only when the modal (re)opens -- once open, the user owns the
  // value and it must not jump around on every live probe frame.
  useEffect(() => {
    if (open) {
      setTemp(clamp(initial));
      setDraft(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const set = (t: number) => {
    setTemp(clamp(t));
    setDraft(null);
  };
  const bump = (d: number) => set(temp + d);

  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onCancel}>
      <Pressable style={styles.scrim} onPress={onCancel}>
        <Pressable style={styles.modal} onPress={(e) => e.stopPropagation()}>
          <Text style={styles.title}>Set Hold Temperature</Text>
          <View style={styles.row}>
            <Pressable
              style={styles.step}
              onPress={() => bump(-step)}
              accessibilityLabel="decrease"
            >
              <Text style={styles.stepText}>{"−"}</Text>
            </Pressable>
            <TextInput
              style={styles.input}
              keyboardType="number-pad"
              value={draft ?? String(temp)}
              onChangeText={(text) => {
                setDraft(text);
                const typed = Number(text);
                if (text.trim() !== "" && Number.isFinite(typed)) setTemp(clamp(typed));
              }}
              onBlur={() => setDraft(null)}
            />
            <Text style={styles.unit}>{"°"}{units}</Text>
            <Pressable style={styles.step} onPress={() => bump(step)} accessibilityLabel="increase">
              <Text style={styles.stepText}>+</Text>
            </Pressable>
          </View>
          <Text style={styles.range}>
            {lo}{"°"} – {hi}{"°"}{units}
          </Text>
          <View style={styles.actions}>
            <Pressable style={styles.button} onPress={onCancel}>
              <Text style={styles.buttonText}>Cancel</Text>
            </Pressable>
            <Pressable
              style={[styles.button, styles.buttonAccent]}
              onPress={() => {
                setDraft(null);
                onSubmit(temp);
              }}
            >
              <Text style={styles.buttonAccentText}>Set Hold</Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.6)",
    alignItems: "center",
    justifyContent: "center",
  },
  modal: {
    backgroundColor: tokens.surface,
    borderRadius: 16,
    padding: 20,
    gap: 12,
    width: "85%",
  },
  title: {
    color: tokens.text,
    fontSize: 16,
    fontWeight: "600",
    textAlign: "center",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  step: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: tokens.background,
  },
  stepText: {
    color: tokens.text,
    fontSize: 20,
  },
  input: {
    minWidth: 70,
    textAlign: "center",
    fontSize: 28,
    fontWeight: "700",
    color: tokens.text,
  },
  unit: {
    color: tokens.text,
    fontSize: 16,
    opacity: 0.7,
  },
  range: {
    color: tokens.text,
    opacity: 0.6,
    fontSize: 12,
    textAlign: "center",
  },
  actions: {
    flexDirection: "row",
    gap: 8,
  },
  button: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 8,
    alignItems: "center",
    backgroundColor: tokens.background,
  },
  buttonText: {
    color: tokens.text,
    fontWeight: "600",
  },
  buttonAccent: {
    backgroundColor: tokens.accent,
  },
  buttonAccentText: {
    color: tokens.background,
    fontWeight: "700",
  },
});
