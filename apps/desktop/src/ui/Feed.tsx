/** WHAT JUST HAPPENED (Main.html): the latest of what the office hears,
 * newest first, each with its dot and how long ago. */
import type { FeedItem } from "../api/types";
import { ago, feedLine, type FeedTone } from "../lib/live";

const DOTS: Record<FeedTone, string> = {
  green: "var(--color-green)", red: "var(--color-red)", amber: "var(--color-amber)",
  violet: "var(--color-violet)", blue: "var(--color-blue)", brown: "#B4844A",
};

export function Feed({ items, now }: { items: FeedItem[] | null; now: Date }) {
  return (
    <section className="ov panel feedpanel" aria-label="What just happened">
      <span className="eyebrow">What just happened</span>
      {items && items.length === 0 && (
        <div className="small">Nothing yet. Deliveries, pre-starts and runs leaving show here as they happen.</div>)}
      {items && items.length > 0 && (
        <ul className="feed">
          {items.map((i, n) => {
            const { text, tone } = feedLine(i);
            return (
              <li key={`${i.kind}-${i.at}-${n}`}>
                <span className="dot" style={{ background: DOTS[tone] }} />
                <span style={{ flex: 1 }}>{text}</span>
                <time dateTime={i.at}>{ago(i.at, now)}</time>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
