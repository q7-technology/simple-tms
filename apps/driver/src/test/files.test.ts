import { indexedDbFiles, memoryFiles, readBytes } from "../lib/files";

describe("pictures kept on the phone", () => {
  it("keeps a picture in IndexedDB until it is deleted", async () => {
    const blob = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" });
    await indexedDbFiles.put("a", blob);
    const back = await indexedDbFiles.get("a");
    expect(back?.type).toBe("image/png");
    expect(new Uint8Array(await readBytes(back!))).toEqual(new Uint8Array([0x89, 0x50, 0x4e, 0x47]));
    await indexedDbFiles.delete("a");
    expect(await indexedDbFiles.get("a")).toBeUndefined();
  });

  it("has a stand-in in memory", async () => {
    const files = memoryFiles();
    await files.put("x", new Blob(["1"]));
    expect(files.size()).toBe(1);
    await files.delete("x");
    expect(await files.get("x")).toBeUndefined();
  });
});
