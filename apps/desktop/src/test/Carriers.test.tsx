import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Manifest } from "../api/types";
import { isoDay } from "../lib/days";
import { line, manifest, PLAINS, RIDGELINE, VALLEY } from "./bay";
import { json, path, renderApp, stubApi, type Call } from "./harness";

const today = isoDay(new Date());
const RX = manifest(1, RIDGELINE, [line(26, "Mill Lane Florist", null), line(33, "Hilltop Joinery", "RX-77310042", 5, "72")],
                    { date: today });
const VF = manifest(2, VALLEY, [line(24, "Paddock Produce", "VF-00418821", 4, "88"), line(28, "Old Mill Books", "VF-00418822", 5, "54")],
                    { date: today, carrier: { id: 2, name: VALLEY, account: null, pickup_time: "14:00", adapter: "manual", active: true, created_at: "" } });
const PP = manifest(3, PLAINS, [line(20, "Corner Hardware", "PP-1001")],
                    { date: today, status: "collected", collected_at: new Date(new Date().setHours(10, 15, 0, 0)).toISOString(),
                      collected_by: "dee", print_job: { job_id: "j", status: "printed", printer: "office", last_error: null, created_at: "", printed_at: "" },
                      shipping: { pending: 0, done: 1, failed: 0 } });

function bay(manifests: Manifest[] = [RX, VF, PP], answers: Record<string, (call: Call) => Response> = {}) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/manifests") return json(200, { items: manifests, total: manifests.length });
    if (key === "GET /v1/carriers") return json(200, { items: [RX.carrier, VF.carrier], total: 2 });
    if (method === "POST") return json(200, RX);
    return null;
  });
}

const group = (name: RegExp) => screen.findByRole("region", { name });

describe("Carriers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("draws what needs a con note, what waits for pickup and what went today", async () => {
    const { calls } = bay();
    renderApp("/carriers");
    expect(await screen.findByRole("dialog", { name: "Carriers" })).toBeInTheDocument();
    const needs = (await screen.findByText("Needs a con note")).parentElement!;
    await waitFor(() => expect(needs).toHaveTextContent("1"));
    expect(needs).toHaveTextContent("0080034526 · Mill Lane Florist");
    expect((screen.getByText("Waiting for pickup")).parentElement).toHaveTextContent("4on the carrier bay · 2 carriers");
    expect((screen.getByText("Collected today")).parentElement).toHaveTextContent(`1${PLAINS} · 10:15 am`);
    const asked = calls.find((c) => path(c.url) === "/v1/manifests")!.url;
    expect(asked).toContain(`date=${today}`);
    // and those from earlier days collected since midnight, by this desk's clock
    expect(new URL(asked, "http://x").searchParams.get("collected_since"))
      .toBe(new Date(new Date().setHours(0, 0, 0, 0)).toISOString());

    const rx = await group(/Ridgeline Express/);
    expect(within(rx).getByText("Pickup about 3:30 pm · 2 consignments · 7 cartons · 96 kg")).toBeInTheDocument();
    expect(within(rx).getByText("1 needs a con note")).toBeInTheDocument();
    expect(within(rx).getByRole("button", { name: /Print manifest/ })).toBeDisabled();
    expect(within(rx).getByText("Add the missing con note to print this manifest.")).toBeInTheDocument();
    const rows = within(rx).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("0080034526Mill Lane FloristDaylesford224Not added yetNeeds con note");
    expect(rows[2]).toHaveTextContent("RX-77310042On the bay");

    const vf = await group(/Valley Freight/);
    expect(within(vf).getByText("Ready")).toBeInTheDocument();
    expect(within(vf).getByRole("button", { name: /Print manifest/ })).toBeEnabled();

    const pp = await group(/Plains Parcels/);
    expect(within(pp).getByText("Collected 10:15 am · manifest M-0003 printed by Platen · marked shipped in Simple WMS")).toBeInTheDocument();
    expect(within(pp).getByRole("button", { name: /Reprint/ })).toBeInTheDocument();
    expect(within(pp).queryByRole("table")).toBeNull();
  });

  it("takes a scanned con note for the first that needs one, then goes on to the next", async () => {
    const more = manifest(4, VALLEY, [line(40, "Two Rivers Vet", null)], { date: today });
    const { calls } = bay([RX, more]);
    renderApp("/carriers");
    const box = await screen.findByRole("form", { name: "Add a con note" });
    expect(within(box).getByText("Con note for 0080034526 · Mill Lane Florist")).toBeInTheDocument();
    expect(within(box).getByRole("combobox", { name: "Carrier" })).toHaveValue("1");
    const user = userEvent.setup();
    // a desk scanner types the label and presses Enter
    await user.type(within(box).getByRole("textbox", { name: "Con note number" }), " rx-77310049{Enter}");
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const sent = calls.find((c) => c.method === "POST")!;
    expect(sent.url).toBe("/v1/manifests/1/con-note");
    expect(sent.body).toEqual({ consignment_id: 26, con_note: "RX-77310049" });
    expect(await screen.findByText("Con note RX-77310049 saved for Mill Lane Florist.")).toBeInTheDocument();
    expect(within(box).getByText("Con note for 0080034540 · Two Rivers Vet")).toBeInTheDocument();
    expect(within(box).getByRole("textbox", { name: "Con note number" })).toHaveValue("");
    expect(within(box).getByRole("textbox", { name: "Con note number" })).toHaveFocus();
  });

  it("books it with another carrier when another is chosen", async () => {
    const { calls } = bay();
    renderApp("/carriers");
    const box = await screen.findByRole("form", { name: "Add a con note" });
    const user = userEvent.setup();
    await user.selectOptions(within(box).getByRole("combobox", { name: "Carrier" }), "2");
    await user.type(within(box).getByRole("textbox", { name: "Con note number" }), "VF-00418830");
    await user.click(within(box).getByRole("button", { name: "Save con note" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const sent = calls.find((c) => c.method === "POST")!;
    expect(sent.url).toBe("/v1/carriers/2/book");
    expect(sent.body).toEqual({ consignment_id: 26, date: today, con_note: "VF-00418830" });
  });

  it("says what is wrong with a con note before sending it, and corrects one from its row", async () => {
    const { calls } = bay();
    renderApp("/carriers");
    const box = await screen.findByRole("form", { name: "Add a con note" });
    const user = userEvent.setup();
    await user.type(within(box).getByRole("textbox", { name: "Con note number" }), "RX 7731{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("A con note is 4 to 40 letters, digits and dashes, as on the carrier's label.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Con note for Hilltop Joinery" }));
    expect(within(box).getByText("Con note for 0080034533 · Hilltop Joinery")).toBeInTheDocument();
    // its con note now is shown, not put in the box, so a scan replaces it
    expect(within(box).getByRole("textbox", { name: "Con note number" })).toHaveValue("");
    expect(within(box).getByText("Now RX-77310042: scan or type the new one.")).toBeInTheDocument();
  });

  it("puts only the new number in when a row that has a con note is picked and scanned", async () => {
    const { calls } = bay();
    renderApp("/carriers");
    const box = await screen.findByRole("form", { name: "Add a con note" });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Con note for Hilltop Joinery" }));
    // the scanner types into the box it was focused on, and presses Enter
    await user.keyboard("RX-77310050{Enter}");
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ consignment_id: 33, con_note: "RX-77310050" });
  });

  it("takes the next scan alone after a refused one, and none mid-save", async () => {
    let answer: (r: Response) => void = () => undefined;
    const { calls } = bay([RX], {
      "POST /v1/manifests/1/con-note": ({ body }) => (body as { con_note: string }).con_note === "RX-77310042"
        ? json(422, { detail: "invalid", errors: [{ field: "con_note", message: "RX-77310042 is already the con note of 0080034533 (Hilltop Joinery) with Ridgeline" }] })
        : (new Promise<Response>((r) => { answer = r; }) as unknown as Response),
    });
    renderApp("/carriers");
    const box = await screen.findByRole("form", { name: "Add a con note" });
    const input = within(box).getByRole("textbox", { name: "Con note number" });
    const user = userEvent.setup();
    await user.keyboard("RX-77310042{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("RX-77310042 is already the con note of 0080034533");
    await waitFor(() => expect(input).toHaveFocus());
    await user.keyboard("RX-77310051{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "POST")[1].body).toEqual({ consignment_id: 26, con_note: "RX-77310051" });
    // while that is saved, the box takes nothing: a scan can't land and be lost
    expect(input).toHaveAttribute("readonly");
    answer(json(200, RX));
    await waitFor(() => expect(input).not.toHaveAttribute("readonly"));
  });

  it("keeps what is being typed for its consignment when the poll changes what needs one first", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const earlier = manifest(5, VALLEY, [line(45, "Two Rivers Vet", null)], { date: today });
      let shown = [RX];
      const { calls } = bay([], { "GET /v1/manifests": () => json(200, { items: shown, total: shown.length }) });
      renderApp("/carriers");
      const box = await screen.findByRole("form", { name: "Add a con note" });
      const input = within(box).getByRole("textbox", { name: "Con note number" });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.keyboard("RX-773");
      // another desk put Two Rivers Vet on the bay ahead of it
      shown = [earlier, RX];
      await act(async () => { vi.advanceTimersByTime(20_000); });
      await waitFor(() => expect(screen.getByRole("region", { name: /Valley Freight/ })).toBeInTheDocument());
      expect(within(box).getByText("Con note for 0080034526 · Mill Lane Florist")).toBeInTheDocument();
      expect(input).toHaveValue("RX-773");
      await user.keyboard("10049{Enter}");
      await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
      expect(calls.find((c) => c.method === "POST")!.body).toEqual({ consignment_id: 26, con_note: "RX-77310049" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("says so, and clears the box, when the consignment being typed for leaves the bay", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let shown = [RX];
      bay([], { "GET /v1/manifests": () => json(200, { items: shown, total: shown.length }) });
      renderApp("/carriers");
      const box = await screen.findByRole("form", { name: "Add a con note" });
      const input = within(box).getByRole("textbox", { name: "Con note number" });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await user.keyboard("RX-773");
      // another desk put Mill Lane Florist on a run, and booked Two Rivers Vet
      shown = [manifest(1, RIDGELINE, [line(45, "Two Rivers Vet", null), RX.lines[1]], { date: today })];
      await act(async () => { vi.advanceTimersByTime(20_000); });
      expect(await screen.findByText("Mill Lane Florist (0080034526) has left the carrier bay, so the con note being typed for it was cleared."))
        .toBeInTheDocument();
      // the half-typed number isn't carried over to another consignment
      expect(within(box).getByText("Con note for 0080034545 · Two Rivers Vet")).toBeInTheDocument();
      expect(input).toHaveValue("");
    } finally {
      vi.useRealTimers();
    }
  });

  it("books one left from an earlier day with another carrier for today", async () => {
    const old = manifest(1, RIDGELINE, [line(26, "Mill Lane Florist", null)], { date: "2026-10-01" });
    const { calls } = bay([old, VF]);
    renderApp("/carriers");
    const box = await screen.findByRole("form", { name: "Add a con note" });
    const user = userEvent.setup();
    await user.selectOptions(within(box).getByRole("combobox", { name: "Carrier" }), "2");
    await user.type(within(box).getByRole("textbox", { name: "Con note number" }), "VF-00418830{Enter}");
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!).toMatchObject({
      url: "/v1/carriers/2/book", body: { consignment_id: 26, date: today, con_note: "VF-00418830" } });
  });

  it("prints a manifest, marks it collected and puts one back in the tray", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = bay();
    renderApp("/carriers");
    const vf = await group(/Valley Freight/);
    const user = userEvent.setup();
    await user.click(within(vf).getByRole("button", { name: /Print manifest/ }));
    expect(await screen.findByText("M-0002 sent to Platen.")).toBeInTheDocument();
    await user.click(within(vf).getByRole("button", { name: /Mark collected/ }));
    expect(confirm).toHaveBeenCalledWith(`${VALLEY} has collected 2 consignments? Simple WMS is told each has shipped with its con note, unless its run already shipped it.`);
    const collect = calls.find((c) => path(c.url) === "/v1/manifests/2/collect")!;
    expect(collect.body).toEqual({ message_id: expect.stringMatching(/^[0-9a-f-]{36}$/), consignment_ids: [24, 28] });
    expect(await screen.findByText(`${VALLEY} has collected M-0002. Simple WMS is being told.`)).toBeInTheDocument();
    await user.click(within(vf).getByRole("button", { name: "Put Old Mill Books back in the tray" }));
    await waitFor(() => expect(calls.some((c) => path(c.url) === "/v1/manifests/2/unbook")).toBe(true));
    expect(calls.find((c) => path(c.url) === "/v1/manifests/2/unbook")!.body).toEqual({ consignment_id: 28 });
    confirm.mockRestore();
  });

  it("says why Simple WMS wasn't told, beside Try again", async () => {
    const stuck = { ...PP, shipping: { pending: 0, done: 0, failed: 1, last_error: "HTTP 503: Simple WMS is down for maintenance" } };
    const { calls } = bay([stuck]);
    renderApp("/carriers");
    const pp = await group(/Plains Parcels/);
    expect(within(pp).getByText("1 not marked shipped in Simple WMS after five tries: HTTP 503: Simple WMS is down for maintenance"))
      .toBeInTheDocument();
    await userEvent.setup().click(within(pp).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(calls.some((c) => path(c.url) === "/v1/manifests/3/retry-ship")).toBe(true));
  });

  it("says plainly when nothing waits, and when there are no carriers yet", async () => {
    stubApi((_m, url) => json(200, { items: [], total: 0 }));
    renderApp("/carriers");
    expect(await screen.findByText(/Nothing is waiting for a carrier/)).toBeInTheDocument();
    expect(await screen.findByText(/No carriers yet\. A fleet manager adds them/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Add a con note" })).toBeNull();
  });
});
