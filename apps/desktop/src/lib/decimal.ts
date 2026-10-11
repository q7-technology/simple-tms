/** Weights and volumes arrive as decimal strings ("9.5"). They are added up
 * in whole millionths with BigInt, so no weight passes through a float.
 * Digits past the sixth decimal place are dropped: a millionth of a cubic
 * metre is a cubic centimetre. Only a bar's width is ever a float. */

const PLACES = 6;
export const ONE = 1_000_000n;

export function toMicro(text: string | null | undefined): bigint {
  if (text == null || text === "") return 0n;
  const m = /^(-)?(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (!m) throw new Error(`not a decimal: ${text}`);
  const frac = (m[3] ?? "").slice(0, PLACES).padEnd(PLACES, "0");
  const value = BigInt(m[2]) * ONE + BigInt(frac);
  return m[1] ? -value : value;
}

export function sum(values: (string | null | undefined)[]): bigint {
  return values.reduce((total, v) => total + toMicro(v), 0n);
}

/** 2380500000n -> "2,380.5": thousands grouped, no trailing zeros. */
export function plain(value: bigint): string {
  const sign = value < 0n ? "-" : "";
  const abs = value < 0n ? -value : value;
  const whole = (abs / ONE).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const frac = (abs % ONE).toString().padStart(PLACES, "0").replace(/0+$/, "");
  return sign + whole + (frac ? `.${frac}` : "");
}

/** Up or down to a whole number of steps (a step in millionths too). */
export function roundUp(value: bigint, step: bigint): bigint {
  const r = ((value % step) + step) % step;
  return r === 0n ? value : value - r + step;
}

export function roundDown(value: bigint, step: bigint): bigint {
  const r = ((value % step) + step) % step;
  return value - r;
}

/** part / whole as a fraction, for drawing a bar. */
export function ratio(part: bigint, whole: bigint): number {
  if (whole <= 0n) return 0;
  return Number((part * 10_000n) / whole) / 10_000;
}
