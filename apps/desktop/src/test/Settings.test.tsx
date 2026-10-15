import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SettingRow } from "../api/types";
import { changesFor } from "../lib/settings";
import { json, MANAGER, path, renderApp, stubApi, type Call } from "./harness";

const ADMIN = { ...MANAGER, id: 3, name: "leighton", display_name: "Leighton", role: "admin", scopes: ["*"] };

const row = (name: string, kind: SettingRow["kind"], value: SettingRow["value"], over: Partial<SettingRow> = {}): SettingRow => ({
  name, env: `TMS_${name.toUpperCase()}`, kind, value, default: kind === "screen" ? value : null,
  is_set: kind === "secret" ? false : value !== "" && value !== null, on_screen: false, changed_by: null, changed_at: null,
  why_file_only: null, ...over,
});

const SETTINGS: SettingRow[] = [
  row("wms_url", "screen", "https://wms.example.com"), row("wms_api_key", "secret", null, { is_set: true }),
  row("wms_webhook_secret", "secret", null), row("wms_timeout_seconds", "screen", 5),
  row("platen_url", "screen", ""), row("platen_key", "secret", null),
  row("run_sheet_printer", "screen", "Office 2", { default: "dispatch", on_screen: true, changed_by: "leighton", changed_at: new Date().toISOString() }),
  row("manifest_printer", "screen", ""),
  row("position_retention_days", "screen", 90), row("eta_minutes_per_stop", "screen", 20),
  row("worker_poll_seconds", "screen", 5), row("worker_http_timeout_seconds", "screen", 10),
  row("database_url", "file", null, { why_file_only: "the API and the worker need it to reach the database at all" }),
  row("secret_key", "file", null, { why_file_only: "it signs every session: a wrong one signs everyone out" }),
  row("map_tile_origin", "file", "https://tiles.example.com", { why_file_only: "Caddy reads it when it starts, for the desktop's Content-Security-Policy" }),
];

function office(answers: Record<string, (c: Call) => Response> = {}, who: object = ADMIN) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/settings") return json(200, SETTINGS);
    if (key === "GET /v1/settings/history") {
      return json(200, [{ id: 2, name: "platen_key", env: "TMS_PLATEN_KEY", action: "set", value: null, secret: true,
                          changed_by: "leighton", changed_at: new Date().toISOString() },
                        { id: 1, name: "run_sheet_printer", env: "TMS_RUN_SHEET_PRINTER", action: "set", value: "Office 2",
                          secret: false, changed_by: "leighton", changed_at: new Date().toISOString() }]);
    }
    return null;
  }, who);
}

describe("Settings", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends what was changed, a secret only when typed", () => {
    expect(changesFor(SETTINGS, { run_sheet_printer: "Office 2", wms_timeout_seconds: " 8 ", wms_api_key: "" })).toEqual({ wms_timeout_seconds: "8" });
    expect(changesFor(SETTINGS, { platen_key: "plt_new" })).toEqual({ platen_key: "plt_new" });
  });

  it("is in the account menu for an admin only", async () => {
    office({}, MANAGER);
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Account/ }));
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
  });

  it("opens from the account menu, shows where each comes from, and never a secret", async () => {
    office();
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Account/ }));
    await user.click(screen.getByRole("link", { name: "Settings" }));
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    const printing = await screen.findByRole("form", { name: "Printing through Platen" });
    expect(within(printing).getByLabelText("Run sheet printer")).toHaveValue("Office 2");
    expect(within(printing).getByLabelText("Run sheet printer")).toHaveAccessibleDescription(/Set here by leighton, Today .*\. \.env says dispatch\./);
    expect(within(printing).getByLabelText("Carrier manifest printer")).toHaveAccessibleDescription(/From \.env \(TMS_MANIFEST_PRINTER\)\./);
    const wms = screen.getByRole("form", { name: "Simple WMS" });
    const key = within(wms).getByLabelText("Simple WMS key");
    expect(key).toHaveAttribute("type", "password");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("placeholder", "Set: type a new one to replace it");
    const fileOnly = screen.getByRole("region", { name: "In .env only" });
    expect(fileOnly).toHaveTextContent("Map tiles' origin TMS_MAP_TILE_ORIGIN · https://tiles.example.com");
    expect(fileOnly).toHaveTextContent("it signs every session");
    expect(screen.getByText("Platen key: replaced")).toBeInTheDocument();
  });

  it("saves a group's changes together, and shows the API's refusal beside the field", async () => {
    const { calls } = office({
      "PATCH /v1/settings": (c) => ((c.body as Record<string, string>).platen_url && !(c.body as Record<string, string>).platen_key
        ? json(422, { errors: [{ field: "platen_key", message: "Type the Platen key again with a new address: a key on file is only sent where it was given for" }] })
        : json(200, SETTINGS)),
    });
    renderApp("/settings");
    const user = userEvent.setup();
    const printing = await screen.findByRole("form", { name: "Printing through Platen" });
    await user.type(within(printing).getByLabelText("Platen job intake"), "https://platen.example.com/intake/jobs");
    await user.click(within(printing).getByRole("button", { name: "Save Printing through Platen" }));
    await waitFor(() => expect(within(printing).getByLabelText("Platen key")).toHaveAccessibleDescription(
      /Type the Platen key again with a new address/));
    await waitFor(() => expect(within(printing).getByLabelText("Platen key")).toHaveFocus());

    await user.type(within(printing).getByLabelText("Platen key"), "plt_new-key");
    await user.click(within(printing).getByRole("button", { name: "Save Printing through Platen" }));
    expect(await screen.findByText("Saved. The API and the worker use it within 5 seconds.")).toHaveAttribute("role", "status");
    expect(calls.filter((c) => c.method === "PATCH").map((c) => c.body)).toEqual([
      { platen_url: "https://platen.example.com/intake/jobs" },
      { platen_url: "https://platen.example.com/intake/jobs", platen_key: "plt_new-key" }]);
    expect(within(printing).getByLabelText("Platen key")).toHaveValue("");
  });

  it("puts one back to .env's", async () => {
    const { calls } = office({ "PATCH /v1/settings": () => json(200, SETTINGS) });
    renderApp("/settings");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Use .env's run sheet printer" }));
    expect(await screen.findByText("Run sheet printer is .env's again.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ run_sheet_printer: null });
  });

  it("tells anyone else it is for an admin, and asks the API nothing", async () => {
    const { calls } = office({}, MANAGER);
    renderApp("/settings");
    expect(await screen.findByText("Settings are for an admin.")).toBeInTheDocument();
    expect(calls.some((c) => path(c.url) === "/v1/settings")).toBe(false);
  });
});
