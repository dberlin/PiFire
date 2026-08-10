import type { ScanResult } from "../../helpers/contracts/wizard.gen";

export interface DiscoveryPanelProps {
  result: ScanResult;
  onPick: (value: string) => void;
  onRefresh: () => void;
  onClose: () => void;
}

function DiscoveryActions({
  onRefresh,
  onClose,
}: Pick<DiscoveryPanelProps, "onRefresh" | "onClose">) {
  return (
    <div className="pf-discovery-actions">
      <button type="button" onClick={onRefresh}>
        Refresh
      </button>
      <button type="button" onClick={onClose}>
        Close
      </button>
    </div>
  );
}

export function DiscoveryPanel({ result, onPick, onRefresh, onClose }: DiscoveryPanelProps) {
  if (result.error) {
    return (
      <div className="pf-discovery-panel">
        <p role="alert">{result.error}</p>
        <DiscoveryActions onRefresh={onRefresh} onClose={onClose} />
      </div>
    );
  }

  const groups = result.groups.filter((group) => group.items.length > 0);

  if (groups.length === 0) {
    return (
      <div className="pf-discovery-panel">
        <p role="alert">No devices found.</p>
        <DiscoveryActions onRefresh={onRefresh} onClose={onClose} />
      </div>
    );
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
      <DiscoveryActions onRefresh={onRefresh} onClose={onClose} />
    </div>
  );
}
