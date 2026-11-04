/** Stroke icons drawn on a 24px grid; they inherit the text colour. */
const PATHS = {
  check: "M5 12.5l4.5 4.5L19 7.5",
  alert: "M12 4l9 16H3L12 4zM12 10v4.5M12 17.2v.3",
  question: "M9 9.2a3 3 0 1 1 4.2 2.8c-.8.4-1.2 1-1.2 1.8v.7M12 17.5v.3",
  power: "M12 3.5v8M7.2 6.5a7 7 0 1 0 9.6 0",
  near: "M4 12h16M14 6l6 6-6 6",
  x: "M6 6l12 12M18 6L6 18",
  dash: "M6 12h12",
  bell: "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15L6 16zM10 20.5h4",
  bellOff: "M6 16V11a6 6 0 0 1 9.4-4.9M18 11v5l1.5 2H8.5M10 20.5h4M4 4l16 16",
  upload: "M12 16V4M7 9l5-5 5 5M5 20h14",
  settings: "M4 7h16M4 17h16M8 4v6M16 14v6",
  zone: "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5M8 8h8v8H8z",
  base: "M4 8l8-4 8 4v8l-8 4-8-4V8zM4 8l8 4 8-4M12 12v8",
  eye: "M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20, strokeWidth = 2.25 }: { name: IconName; size?: number; strokeWidth?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="square"
      strokeLinejoin="miter"
      aria-hidden="true"
      className="shrink-0"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
