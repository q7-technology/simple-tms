import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { saveTicks } from "../lib/run";
import { json, onThePhone, renderApp, signedIn, stubApi } from "./harness";

vi.mock("../lib/picture", () => ({
  shrinkPhoto: async () => new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" }),
  toBlob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
}));

describe("proof of delivery", () => {
  beforeEach(() => {
    signedIn();
    onThePhone();
    saveTicks(3, 7, 31, { 1: "scan", 2: "tick" });
    URL.createObjectURL = vi.fn(() => "blob:photo");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("sends the photo, then the delivery, and says Ding!", async () => {
    const { calls } = stubApi((method, url) => {
      if (method === "PUT") return json(201, {});
      if (url === "/v1/runs/7/stops/31/deliver") return json(200, { outcome: "delivered" });
      return json(200, { ...{}, id: 7 });
    });
    renderApp("/run/7/stop/31/proof");
    const user = userEvent.setup();
    expect(await screen.findByRole("button", { name: "Confirm delivery" })).toBeDisabled();
    await user.upload(screen.getByLabelText(/Add a photo/), new File(["x"], "door.jpg", { type: "image/jpeg" }));
    expect(await screen.findByRole("img", { name: "The photo taken" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm delivery" }));

    const ding = await screen.findByRole("status", { name: "Delivered" });
    expect(ding).toHaveTextContent("Ding!");
    expect(ding).toHaveTextContent("Stop 1 delivered · 1 of 2 done");
    expect(ding).toHaveTextContent("2 cartons · photo taken");
    expect(screen.getByRole("link", { name: /Next: stop 2 · Two Rivers Vet/ })).toHaveAttribute("href", "/run/7/stop/32");

    await waitFor(() => expect(calls.some((c) => c.url === "/v1/runs/7/stops/31/deliver")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toMatch(/^\/v1\/pod-files\/[0-9a-f-]{36}\?kind=photo$/);
    expect(put.contentType).toBe("image/jpeg");
    const deliver = calls.find((c) => c.url === "/v1/runs/7/stops/31/deliver")!.body as Record<string, unknown>;
    expect(deliver).toEqual({
      message_id: expect.any(String), at: expect.any(String), photo_file: put.url.split("/")[3].split("?")[0],
      packages: [{ package_no: 1, how: "scan" }, { package_no: 2, how: "tick" }],
    });
  });

  it("goes back to the stop until every carton is ticked", async () => {
    saveTicks(3, 7, 31, { 1: "tick" });
    stubApi();
    renderApp("/run/7/stop/31/proof");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/run\/7\/stop\/31$/));
  });
});
