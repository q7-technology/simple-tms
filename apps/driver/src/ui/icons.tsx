/** The line icons the driver screens use, drawn as in design/screens. */
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

export function Icon({ name, size = 20, width = 2 }: { name: keyof typeof PATHS; size?: number; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth={width} aria-hidden="true" {...base}>
      {PATHS[name]}
    </svg>
  );
}

const PATHS = {
  truck: <><path d="M2 6h12v10H2Z" /><path d="M14 9h4.5L22 12.5V16h-8Z" /><circle cx="6.5" cy="17.5" r="2" /><circle cx="17.5" cy="17.5" r="2" /></>,
  back: <><path d="m12 19-7-7 7-7" /><path d="M19 12H5" /></>,
  go: <path d="M5 12h14M13 6l6 6-6 6" />,
  check: <path d="m5 12 5 5 9-10" />,
  map: <><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2Z" /><path d="M9 4v14M15 6v14" /></>,
  pin: <path d="M3 11 21 3l-8 18-2-8Z" />,
  scan: <><path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" /><path d="M8 7v10M12 7v10M16 7v10" /></>,
  note: <><path d="M5 4h14v12l-4 4H5Z" /><path d="M15 20v-4h4M9 9h6M9 13h4" /></>,
  camera: <><path d="M4 8h3l2-3h6l2 3h3v11H4Z" /><circle cx="12" cy="13" r="3.5" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
  nosignal: <><path d="M2 20h.01M7 20v-4M12 20v-8M17 20V8M22 4v16" /><path d="M3 3l18 18" /></>,
  delete: <><path d="M9 6h11v12H9l-6-6Z" /><path d="m12 10 4 4M16 10l-4 4" /></>,
  closed: <><path d="M5 21V4h11v17M3 21h18" /><path d="M13 12h.01" /></>,
  refused: <><circle cx="12" cy="12" r="9" /><path d="m5.6 5.6 12.8 12.8" /></>,
  damaged: <><path d="M12 3 20 7.5v9L12 21 4 16.5v-9Z" /><path d="m10 8 2 3-2 2 2 3" /></>,
  wrong: <path d="M12 3v18M5 6h11l3 3-3 3H5Z" />,
  returns: <><path d="M9 14 4 9l5-5" /><path d="M4 9h10a6 6 0 0 1 0 12h-3" /></>,
  phone: <path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2" />,
};
