import type { ScanResult } from "../../helpers/wizard/wizardTypes";

export interface DiscoveryPanelProps {
  result: ScanResult;
  onPick: (value: string) => void;
}

export function DiscoveryPanel({ result, onPick }: DiscoveryPanelProps) {
  if (result.error) {
    return <p role="alert">{result.error}</p>;
  }

  const groups = result.groups.filter((group) => group.items.length > 0);

  if (groups.length === 0) {
    return <p role="alert">No devices found.</p>;
  }

  return (
    <div className="pf-discovery-panel">
      {groups.map((group) => (
        <div className="pf-discovery-group" key={group.title}>
          <h4 className="pf-discovery-group-title">{group.title}</h4>
          <div className="pf-discovery-group-items">
            {group.items.map((item) => (
              <button type="button" key={item.value} onClick={() => onPick(item.value)}>
                {item.label}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
