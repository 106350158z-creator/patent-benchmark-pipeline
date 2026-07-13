import type { ReviewDocument } from "./types";

const DB_NAME = "local-patent-review";
const DB_VERSION = 1;
const REVIEW_STORE = "reviews";
const SETTINGS_STORE = "settings";

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(REVIEW_STORE)) db.createObjectStore(REVIEW_STORE);
      if (!db.objectStoreNames.contains(SETTINGS_STORE)) db.createObjectStore(SETTINGS_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function put(store: string, key: IDBValidKey, value: unknown): Promise<void> {
  const db = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(store, "readwrite");
    transaction.objectStore(store).put(value, key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
  db.close();
}

async function get<T>(store: string, key: IDBValidKey): Promise<T | undefined> {
  const db = await openDatabase();
  const value = await new Promise<T | undefined>((resolve, reject) => {
    const request = db.transaction(store, "readonly").objectStore(store).get(key);
    request.onsuccess = () => resolve(request.result as T | undefined);
    request.onerror = () => reject(request.error);
  });
  db.close();
  return value;
}

export function saveReview(review: ReviewDocument): Promise<void> {
  return put(REVIEW_STORE, review.application_number, review);
}

export function loadReview(applicationNumber: string): Promise<ReviewDocument | undefined> {
  return get<ReviewDocument>(REVIEW_STORE, applicationNumber);
}

export function saveRootHandle(handle: FileSystemDirectoryHandle): Promise<void> {
  return put(SETTINGS_STORE, "root-handle", handle);
}

export function loadRootHandle(): Promise<FileSystemDirectoryHandle | undefined> {
  return get<FileSystemDirectoryHandle>(SETTINGS_STORE, "root-handle");
}
