import type { ProbeHealthSummary, ProbeHealthView } from "@pifire/core/dashboard/probeHealth";
import { StyleSheet, Text, View } from "react-native";
import {
  BODY_TEXT_COLOR,
  DANGER,
  PROBE_LABEL_COLOR,
  THEME,
  WARN_COLOR,
  withAlpha,
} from "../theme";

interface HealthProps {
  health: ProbeHealthView | null;
}

function accessibilityCopy(health: ProbeHealthView, additionalCopy?: string | null): string {
  const severity = health.severity === "danger" ? "Danger" : "Warning";
  return [
    severity,
    health.displayName,
    health.headline,
    health.impactCopy,
    health.causeCopy,
    additionalCopy,
    health.freshnessQualifier,
  ]
    .filter((part): part is string => part !== null && part !== undefined)
    .join(". ");
}

function HealthIcon({ danger }: { danger: boolean }) {
  return (
    <View
      style={[styles.icon, { borderColor: danger ? DANGER : WARN_COLOR }]}
      accessible={false}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      <Text style={[styles.iconText, { color: danger ? DANGER : WARN_COLOR }]}>!</Text>
    </View>
  );
}

function HealthCopy({
  health,
  additionalCopy,
}: {
  health: ProbeHealthView;
  additionalCopy?: string | null;
}) {
  return (
    <View style={styles.copy}>
      <View style={styles.headlineRow}>
        <Text style={[styles.headline, { color: health.severity === "danger" ? DANGER : WARN_COLOR }]}>
          {health.headline}
        </Text>
        {additionalCopy ? <Text style={styles.additional}>{additionalCopy}</Text> : null}
      </View>
      {health.impactCopy ? <Text style={styles.body}>{health.impactCopy}</Text> : null}
      {health.causeCopy ? <Text style={styles.cause}>{health.causeCopy}</Text> : null}
      {health.freshnessQualifier ? (
        <Text style={[styles.freshness, { color: health.severity === "danger" ? DANGER : WARN_COLOR }]}>
          {health.freshnessQualifier}
        </Text>
      ) : null}
    </View>
  );
}

/** Persistent, non-dismissible shell treatment for an active Primary confirmation. */
export function HealthBanner({ health }: HealthProps) {
  if (health?.state !== "confirmed") return null;

  return (
    <View
      accessible
      testID="primary-health-banner"
      accessibilityRole="alert"
      accessibilityLiveRegion="assertive"
      accessibilityLabel={accessibilityCopy(health)}
      style={[styles.banner, { backgroundColor: withAlpha(DANGER, 0.16), borderColor: DANGER }]}
    >
      <HealthIcon danger />
      <View style={styles.bannerCopy}>
        <Text style={styles.probeName}>{health.displayName}</Text>
        <HealthCopy health={health} />
      </View>
    </View>
  );
}

/** In-card context for suspected or confirmed health; quiet states render nothing. */
export function ProbeHealthInline({ health }: HealthProps) {
  if (!health || health.priority === 0) return null;
  const danger = health.severity === "danger";

  return (
    <View
      accessible
      testID="probe-health-inline"
      accessibilityRole="alert"
      accessibilityLiveRegion={danger ? "assertive" : "polite"}
      accessibilityLabel={accessibilityCopy(health)}
      style={[
        styles.inline,
        {
          backgroundColor: withAlpha(danger ? DANGER : WARN_COLOR, 0.12),
          borderColor: danger ? DANGER : WARN_COLOR,
        },
      ]}
    >
      <HealthIcon danger={danger} />
      <HealthCopy health={health} />
    </View>
  );
}

/** Dashboard aggregate. It includes Aux without creating an Aux temperature card. */
export function HealthSummary({ summary }: { summary: ProbeHealthSummary | null }) {
  if (!summary) return null;
  const { highest } = summary;
  const danger = highest.severity === "danger";

  return (
    <View
      testID="health-summary"
      accessibilityRole="summary"
      accessible
      accessibilityLabel={accessibilityCopy(highest, summary.additionalCopy)}
      style={[
        styles.summary,
        {
          backgroundColor: withAlpha(danger ? DANGER : WARN_COLOR, 0.1),
          borderColor: danger ? DANGER : WARN_COLOR,
        },
      ]}
    >
      <HealthIcon danger={danger} />
      <View style={styles.summaryCopy}>
        <Text style={styles.probeName}>{highest.displayName}</Text>
        <HealthCopy health={highest} additionalCopy={summary.additionalCopy} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    width: "100%",
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderTopWidth: 1,
    borderBottomWidth: 1,
  },
  bannerCopy: {
    flex: 1,
    flexShrink: 1,
    gap: 3,
  },
  inline: {
    width: "100%",
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    marginTop: 8,
    padding: 10,
    borderRadius: 12,
    borderWidth: 1,
  },
  summary: {
    width: "100%",
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    padding: 14,
    borderRadius: 16,
    borderWidth: 1,
  },
  summaryCopy: {
    flex: 1,
    flexShrink: 1,
    gap: 3,
  },
  icon: {
    width: 22,
    height: 22,
    flexShrink: 0,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 11,
    borderWidth: 2,
  },
  iconText: {
    fontSize: 14,
    lineHeight: 16,
    fontWeight: "900",
  },
  copy: {
    flex: 1,
    flexShrink: 1,
    gap: 3,
  },
  headlineRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "baseline",
    gap: 8,
  },
  headline: {
    flexShrink: 1,
    fontSize: 13,
    fontWeight: "800",
    letterSpacing: 1.1,
  },
  additional: {
    color: PROBE_LABEL_COLOR,
    fontSize: 13,
    fontWeight: "700",
  },
  probeName: {
    color: THEME.ember.text,
    fontSize: 14,
    fontWeight: "700",
  },
  body: {
    color: BODY_TEXT_COLOR,
    flexShrink: 1,
    fontSize: 13,
    lineHeight: 18,
  },
  cause: {
    color: PROBE_LABEL_COLOR,
    flexShrink: 1,
    fontSize: 12,
    lineHeight: 17,
  },
  freshness: {
    flexShrink: 1,
    fontSize: 12,
    fontWeight: "700",
  },
});
