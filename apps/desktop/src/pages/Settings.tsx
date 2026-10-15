import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { SettingChange, SettingRow } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { apiProblems, said, type Problems } from "../lib/admin";
import { when } from "../lib/fleet";
import { changesFor, COPY, GROUPS, KEY_FOR, shown } from "../lib/settings";
import { Drawer } from "../ui/Drawer";
import { Field, focusFirstProblem } from "../ui/Field";

type Note = { tone: "r" | "g"; text: string } | null;

function source(s: SettingRow): string {
  if (!s.on_screen) return `From .env (${s.env}).`;
  const was = s.kind === "secret" ? "" : ` .env says ${s.default === "" || s.default === null ? "nothing" : String(s.default)}.`;
  return `Set here by ${s.changed_by}, ${when(s.changed_at!)}.${was}`;
}

/** One group of settings, saved together. */
function Group({ title, rows, onSaved, onReset }: {
  title: string; rows: SettingRow[];
  onSaved: (rows: SettingRow[]) => void; onReset: (s: SettingRow) => Promise<void>;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<Problems>({});
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // a refused Use .env's, said beside its button
  const [refused, setRefused] = useState<{ at: string; text: string } | null>(null);
  const form = useRef<HTMLFormElement>(null);

  async function reset(s: SettingRow) {
    setRefused(null);
    setBusy(true);
    try {
      await onReset(s);
    } catch (err) {
      const theirs = apiProblems(err);
      setRefused({ at: s.name, text: (theirs && (theirs[s.name] ?? Object.values(theirs)[0])) || said(err) });
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTrouble(null);
    setProblems({});
    const body = changesFor(rows, drafts);
    if (!Object.keys(body).length) { setTrouble("Nothing here has changed."); return; }
    setBusy(true);
    try {
      onSaved(await api.patch<SettingRow[]>("/v1/settings", body));
      setDrafts({});
    } catch (err) {
      const theirs = apiProblems(err);
      if (theirs) {
        const { body: whole, ...fields } = theirs;
        setProblems(fields);
        if (whole) setTrouble(whole);
        focusFirstProblem(form.current);
      } else setTrouble(said(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form ref={form} className="adminform" aria-label={title} noValidate onSubmit={(e) => void submit(e)}>
      <div className="eyebrow">{title}</div>
      {rows.map((s) => {
        const copy = COPY[s.name] ?? { label: s.env };
        const secret = s.kind === "secret";
        const key = rows.find((k) => k.name === KEY_FOR[s.name]);
        const withKey = s.on_screen && key?.on_screen ? key : null;
        const errId = `setting-${s.name}-refused`;
        const why = refused?.at === s.name ? refused.text : null;
        return (
          <div key={s.name} className="acts" style={{ alignItems: "flex-end" }}>
            <div style={{ flex: "1 1 280px" }}>
              <Field label={copy.label} name={s.name} value={drafts[s.name] ?? shown(s)} error={problems[s.name]}
                     type={secret ? "password" : "text"} autoComplete={secret ? "new-password" : "off"}
                     inputMode={copy.numeric ? "decimal" : undefined} spellCheck={false}
                     placeholder={secret ? (s.is_set ? "Set: type a new one to replace it" : "Not set") : undefined}
                     onChange={(e) => setDrafts((d) => ({ ...d, [s.name]: e.target.value }))}
                     hint={<>{copy.hint ? `${copy.hint} ` : ""}{secret ? "Never shown again. " : ""}{source(s)}
                       {withKey ? ` Use .env's puts the ${COPY[withKey.name]?.label ?? withKey.env} back too.` : ""}</>} />
            </div>
            {s.on_screen && (
              <button type="button" className="btn sm" disabled={busy} aria-label={`Use .env's ${copy.label.toLowerCase()}`}
                      aria-describedby={why ? errId : undefined} onClick={() => void reset(s)}>Use .env's</button>)}
            {why && <div id={errId} className="msg r" role="alert" style={{ flexBasis: "100%" }}>{why}</div>}
          </div>
        );
      })}
      {trouble && <div className="msg r" role="alert">{trouble}</div>}
      <div className="acts">
        <button type="submit" className="btn primary" disabled={busy} aria-label={`Save ${title}`}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </form>
  );
}

function historyLine(c: SettingChange): string {
  const label = COPY[c.name]?.label ?? c.env;
  if (c.action === "reset") return `${label}: back to .env's`;
  return c.secret ? `${label}: replaced` : `${label}: ${c.value === "" ? "emptied" : c.value}`;
}

/** Settings an admin may change without editing .env (GET and PATCH
 * /v1/settings). Opened from the account menu; admins only. */
export function Settings() {
  const { can } = useAuth();
  const admin = can("settings:write");
  const [rows, setRows] = useState<SettingRow[] | null>(null);
  const [history, setHistory] = useState<SettingChange[]>([]);
  const [note, setNote] = useState<Note>(null);

  const load = useCallback(async () => {
    const [s, h] = await Promise.all([api.get<SettingRow[]>("/v1/settings"),
                                      api.get<SettingChange[]>("/v1/settings/history", { limit: 10 })]);
    setRows(s);
    setHistory(h);
  }, []);

  useEffect(() => {
    if (admin) load().catch((e) => setNote({ tone: "r", text: said(e) }));
  }, [admin, load]);

  async function saved(fresh: SettingRow[]) {
    setRows(fresh);
    setNote({ tone: "g", text: "Saved. The API and the worker use it within 5 seconds." });
    await load().catch(() => undefined);
  }

  /** Throws a refusal, for the group to say beside the button. */
  async function reset(s: SettingRow) {
    const label = (r: SettingRow) => COPY[r.name]?.label ?? r.env;
    const key = all.find((k) => k.name === KEY_FOR[s.name] && k.on_screen);
    setNote(null);
    await saved(await api.patch<SettingRow[]>("/v1/settings", { [s.name]: null }));
    setNote({ tone: "g", text: key ? `${label(s)} and ${label(key)} are .env's again.` : `${label(s)} is .env's again.` });
  }

  const all = rows ?? [];
  const fileOnly = all.filter((s) => s.kind === "file");
  return (
    <Drawer label="Settings" head={
      <div style={{ flex: 1, minWidth: 200 }}>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Admin</div>
        <h1>Settings</h1>
        <div className="sub">Change these here instead of in .env. .env gives each its default; Use .env's puts one back.</div>
      </div>
    }>
      {!admin && <div className="msg a" role="status">Settings are for an admin.</div>}
      {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}
      {rows && GROUPS.map((g) => (
        <Group key={g.title} title={g.title} rows={g.names.map((n) => all.find((s) => s.name === n)).filter((s): s is SettingRow => !!s)}
               onSaved={(fresh) => void saved(fresh)} onReset={reset} />))}
      {rows && (
        <section className="adminform" aria-label="In .env only">
          <div className="eyebrow">In .env only</div>
          <div className="small">These could lock everyone out, break the app or weaken security, or something else reads them
            when it starts. Change them in .env and restart: <span className="mono">docker compose up -d</span>.</div>
          <div className="box">
            {fileOnly.map((s) => (
              <div key={s.name} style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                <span><b>{COPY[s.name]?.label ?? s.env}</b> <span className="mono">{s.env}</span>{s.value !== null && s.value !== "" &&
                  <>{" · "}<span className="mono" style={{ wordBreak: "break-all" }}>{String(s.value)}</span></>}</span>
                <span className="small">{s.why_file_only}</span>
              </div>))}
            <div style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
              <span><b>Ports, domain and database password</b> <span className="mono">TMS_DOMAIN, TMS_HTTP_PORT, TMS_HTTPS_PORT, TMS_API_PORT, POSTGRES_*</span></span>
              <span className="small">Compose and Caddy read them when they start.</span>
            </div>
          </div>
        </section>
      )}
      {history.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div className="eyebrow">RECENT CHANGES</div>
          <div className="box">
            {history.map((c) => (
              <div key={c.id}><span>{historyLine(c)}</span>
                <span className="small" style={{ whiteSpace: "nowrap" }}>{when(c.changed_at)} · {c.changed_by}</span></div>))}
          </div>
        </div>
      )}
    </Drawer>
  );
}
