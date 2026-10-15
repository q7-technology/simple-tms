import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

/** A labelled field with its hint and its problem under it, both read out
 * with it (aria-describedby), and marked invalid while it has a problem.
 * The admin forms check for themselves (noValidate), so the browser's own
 * bubbles never stand in for these. */
function useDescribed(hint?: ReactNode, error?: string | null) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-err` : ""].filter(Boolean).join(" ") || undefined;
  const below = (
    <>
      {hint && <div id={`${id}-hint`} className="small">{hint}</div>}
      {error && <div id={`${id}-err`} className="ferr">{error}</div>}
    </>
  );
  return { id, describedBy, below };
}

export function Field({ label, hint, error, ...input }: {
  label: string; hint?: ReactNode; error?: string | null;
} & InputHTMLAttributes<HTMLInputElement>) {
  const { id, describedBy, below } = useDescribed(hint, error);
  return (
    <div className="fld">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="field" type="text" autoComplete="off" aria-invalid={error ? true : undefined}
             aria-describedby={describedBy} {...input} />
      {below}
    </div>
  );
}

export function SelectField({ label, hint, error, children, ...select }: {
  label: string; hint?: ReactNode; error?: string | null; children: ReactNode;
} & SelectHTMLAttributes<HTMLSelectElement>) {
  const { id, describedBy, below } = useDescribed(hint, error);
  return (
    <div className="fld">
      <label htmlFor={id}>{label}</label>
      <select id={id} className="field" aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...select}>
        {children}
      </select>
      {below}
    </div>
  );
}

/** Once a form's problems are on screen, the first one gets the focus, so a
 * keyboard or screen reader user lands on what to put right. */
export function focusFirstProblem(form: HTMLFormElement | null) {
  setTimeout(() => form?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0);
}
