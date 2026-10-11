import { Drawer } from "../ui/Drawer";

/** A drawer whose screen comes with a later build step. Says so, plainly. */
export function Later({ title, step, what }: { title: string; step: string; what: string }) {
  return (
    <Drawer label={title} head={<div style={{ flex: 1 }}><h1>{title}</h1><div className="sub">{what}</div></div>}>
      <div className="msg a" role="status">This screen arrives with {step}. Until then, the API has what there is.</div>
    </Drawer>
  );
}
