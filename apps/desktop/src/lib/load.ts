/** How full a run is, worked out here the way the API works it out
 * (api/tms/services/load.py), so a bar moves the moment a card is dropped.
 * The API still decides: it refuses to print or depart a run that is over. */
import { ONE, plain, ratio, roundDown, roundUp, sum, toMicro } from "./decimal";

export type LoadState = "ok" | "near" | "over";

export interface Gauge {
  used: bigint;
  max: bigint;
  state: LoadState;
  /** 0 to 1, for the bar; a run over its limit draws a full bar. */
  fill: number;
}

export interface LoadView {
  weight: Gauge;
  volume: Gauge;
  state: LoadState;
  /** Over: the bold part ("280 kg too heavy.") and what to do about it. */
  headline: string | null;
  message: string | null;
}

const KG = ONE;
const M3 = ONE / 10n;

function gauge(used: bigint, max: bigint): Gauge {
  // 90 % or more is nearly full: used * 10 >= max * 9, in whole numbers
  const state: LoadState = used > max ? "over" : used * 10n >= max * 9n ? "near" : "ok";
  return { used, max, state, fill: Math.min(1, ratio(used, max)) };
}

export function loadOf(items: { weight_kg: string; volume_m3: string }[],
                       maxKg: string, maxM3: string): LoadView {
  const weight = gauge(sum(items.map((i) => i.weight_kg)), toMicro(maxKg));
  const volume = gauge(sum(items.map((i) => i.volume_m3)), toMicro(maxM3));
  if (weight.state === "over" || volume.state === "over") {
    const parts = [];
    if (weight.state === "over") parts.push(`${plain(roundUp(weight.used - weight.max, KG))} kg too heavy`);
    if (volume.state === "over") parts.push(`${plain(roundUp(volume.used - volume.max, M3))} m³ too big`);
    return {
      weight, volume, state: "over", headline: `${parts.join(" and ")}.`,
      message: "Move a stop to another run before printing the run sheet.",
    };
  }
  if (weight.state === "near" || volume.state === "near") {
    const kg = plain(roundDown(weight.max - weight.used, KG));
    const m3 = plain(roundDown(volume.max - volume.used, M3));
    return { weight, volume, state: "near", headline: null,
             message: `${kg} kg and ${m3} m³ left. Small drops only.` };
  }
  return { weight, volume, state: "ok", headline: null, message: null };
}

/** What dropping one more consignment on a run would do, for "Drop here". */
export function afterAdding(items: { weight_kg: string; volume_m3: string }[],
                            extra: { weight_kg: string; volume_m3: string },
                            maxKg: string, maxM3: string): LoadState {
  return loadOf([...items, extra], maxKg, maxM3).state;
}
