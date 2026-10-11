/** Signatures and photos waiting to be sent, kept on the phone in IndexedDB:
 * localStorage holds a few megabytes of text, and a run's photos are more. */

export interface FileStore {
  put(id: string, blob: Blob): Promise<void>;
  get(id: string): Promise<Blob | undefined>;
  delete(id: string): Promise<void>;
}

const DB = "tms-driver";
const STORE = "files";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => { req.result.createObjectStore(STORE); };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function run<T>(mode: IDBTransactionMode, act: (store: IDBObjectStore) => IDBRequest): Promise<T> {
  return open().then((db) => new Promise<T>((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const req = act(tx.objectStore(STORE));
    // a write is kept only once its transaction is: wait for that, not the request
    tx.oncomplete = () => { db.close(); resolve(req.result as T); };
    tx.onerror = () => { db.close(); reject(tx.error); };
    tx.onabort = () => { db.close(); reject(tx.error); };
  }));
}

/** Stored as bytes with their type: some browsers can't keep a Blob in IndexedDB. */
interface Stored { type: string; bytes: ArrayBuffer }

/** A Blob's bytes, by FileReader, which every browser (and jsdom) has. */
export function readBytes(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

export const indexedDbFiles: FileStore = {
  async put(id, blob) {
    const stored: Stored = { type: blob.type, bytes: await readBytes(blob) };
    await run("readwrite", (s) => s.put(stored, id));
  },
  async get(id) {
    const stored = await run<Stored | undefined>("readonly", (s) => s.get(id));
    return stored ? new Blob([stored.bytes], { type: stored.type }) : undefined;
  },
  async delete(id) {
    await run("readwrite", (s) => s.delete(id));
  },
};

/** For tests, and for a browser with no IndexedDB at all. */
export function memoryFiles(): FileStore & { size(): number } {
  const map = new Map<string, Blob>();
  return {
    put: async (id, blob) => { map.set(id, blob); },
    get: async (id) => map.get(id),
    delete: async (id) => { map.delete(id); },
    size: () => map.size,
  };
}
