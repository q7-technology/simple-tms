import { useEffect, useState } from "react";
import { api } from "../api/client";

/** A driver's photo. An <img> can't send the session's token, so the bytes
 * are fetched with it and shown from a blob: URL, let go when it is gone. */
export function Photo({ fileId, label }: { fileId: string; label: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    let made: string | null = null;
    api.blob(`/v1/pod-files/${fileId}`)
      .then((b) => { if (live) { made = URL.createObjectURL(b); setUrl(made); } })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [fileId]);
  if (failed) return <span className="small">The photo couldn't be loaded.</span>;
  if (!url) return <span className="small">Loading the photo…</span>;
  return <img src={url} alt={label} width={120} height={80} style={{ objectFit: "cover", borderRadius: 10, display: "block", flex: "none" }} />;
}
