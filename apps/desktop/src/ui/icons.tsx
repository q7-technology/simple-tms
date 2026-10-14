/** The few line icons the screens use, drawn as in design/screens. */
const base = { fill: "none", stroke: "currentColor", strokeLinecap: "round", strokeLinejoin: "round" } as const;

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M16 3 28 10 16 17 4 10Z" fill="#5BC28F" />
      <path d="M4 10 16 17V30L4 23Z" fill="#1F8A5B" />
      <path d="M28 10 16 17V30L28 23Z" fill="#146B45" />
      <path d="M10 6.5 22 13.5v4" fill="none" stroke="#E7F4EC" strokeWidth="2" />
    </svg>
  );
}

export function Icon({ name, size = 18, width = 2 }: { name: keyof typeof PATHS; size?: number; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth={width} aria-hidden="true" {...base}>
      {PATHS[name]}
    </svg>
  );
}

const PATHS = {
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  print: <><path d="M7 9V3h10v6M7 17H4v-7h16v7h-3" /><path d="M7 14h10v7H7Z" /></>,
  grip: <><circle cx="9" cy="6" r="1.2" /><circle cx="15" cy="6" r="1.2" /><circle cx="9" cy="12" r="1.2" /><circle cx="15" cy="12" r="1.2" /><circle cx="9" cy="18" r="1.2" /><circle cx="15" cy="18" r="1.2" /></>,
  up: <path d="m6 15 6-6 6 6" />,
  down: <path d="m6 9 6 6 6-6" />,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
  warn: <><path d="M12 3 2 20h20Z" /><path d="M12 10v4M12 17h.01" /></>,
  ban: <><circle cx="12" cy="12" r="9" /><path d="m5.6 5.6 12.8 12.8" /></>,
  route: <><circle cx="6" cy="19" r="2" /><circle cx="18" cy="5" r="2" /><path d="M8 19h8a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h8" /></>,
  box: <><path d="M12 3 20 7.5v9L12 21 4 16.5v-9Z" /><path d="m4 7.5 8 4.5 8-4.5M12 12v9" /></>,
  hand: <><path d="M3 11l4-4 4 2 3-2 7 5-3 3" /><path d="M7 7v6l4 4 3-1 4-2" /></>,
  truck: <><path d="M2 6h12v10H2Z" /><path d="M14 9h4.5L22 12.5V16h-8Z" /><circle cx="6.5" cy="17.5" r="2" /><circle cx="17.5" cy="17.5" r="2" /></>,
  go: <path d="M5 12h14M13 6l6 6-6 6" />,
  tick: <path d="m5 12 5 5 9-10" />,
  target: <><circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></>,
  wrench: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4Z" />,
};
