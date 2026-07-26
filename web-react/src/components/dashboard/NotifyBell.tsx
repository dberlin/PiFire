// The affordance that opens a probe's notification modal (target, high limit,
// low limit). Filled and accented while ANY of the three is armed, muted and
// struck through when none is -- the same two states as the Flask bell button
// (_macro_dash_default.html:108-121, btn-primary vs btn-outline + fa-bell-slash).
//
// Its own module because the food-probe card and the primary probe both need
// it, and they live in different files.
export function NotifyBell({
  probeName,
  on,
  onClick,
}: {
  probeName: string;
  on: boolean;
  onClick(): void;
}) {
  return (
    <button
      type="button"
      className={`pf-notify-bell${on ? " on" : ""}`}
      aria-label={`Notifications for ${probeName}`}
      aria-pressed={on}
      onClick={onClick}
    >
      <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true" focusable="false">
        <path
          d="M12 3a5.5 5.5 0 0 0-5.5 5.5v3.2L5 15.2h14l-1.5-3.5V8.5A5.5 5.5 0 0 0 12 3Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
        <path
          d="M10.2 17.6a1.9 1.9 0 0 0 3.6 0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
        />
        {!on && (
          <path
            d="M4.5 4.5 19.5 19.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        )}
      </svg>
    </button>
  );
}
