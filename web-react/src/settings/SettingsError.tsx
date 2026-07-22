import { useNavigate } from "react-router";

export function SettingsError() {
  const navigate = useNavigate();
  return (
    <div className="pf-fit">
      <div className="pf-settings-error">
        Couldn't load settings.
        <button className="pf-modal-btn" onClick={() => navigate(0)}>Retry</button>
        <button className="pf-modal-btn" onClick={() => navigate("/")}>Dashboard</button>
      </div>
    </div>
  );
}
