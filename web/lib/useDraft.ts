"use client";

import { useEffect, useState } from "react";

type SavedDraft = { value: string; revision: string; attemptId?: string; attemptValue?: string };
const memory = new Map<string, SavedDraft>();
const unavailableScopes = new Set<string>();

export function newRequestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function completeDraftAttempt(scope: string, revision: string): void {
  const prefix = `mindmorph.draft.${encodeURIComponent(scope)}`;
  const key = `${prefix}.${encodeURIComponent(revision)}`;
  const latestKey = `${prefix}.latest`;
  const saved = memory.get(key);
  if (!saved) return;
  const next = { value: saved.value, revision };
  memory.set(key, next);
  memory.set(latestKey, next);
  try {
    localStorage.setItem(key, JSON.stringify(next));
    localStorage.setItem(latestKey, JSON.stringify(next));
  } catch {
    unavailableScopes.add(prefix);
  }
}

// Scope includes learner and resource identity. Revision is part of the storage key so regenerated
// work cannot inherit an old answer or its attempt binding. Memory remains authoritative if storage
// is disabled or throws, including after the editor unmounts.
export function useDraft(scope: string, revision = "legacy") {
  const prefix = `mindmorph.draft.${encodeURIComponent(scope)}`;
  const key = `${prefix}.${encodeURIComponent(revision)}`;
  const latestKey = `${prefix}.latest`;
  const [initial] = useState(() => {
    const saved = memory.get(key);
    const previous = memory.get(latestKey);
    const valid = saved && typeof saved.value === "string" && saved.revision === revision;
    return { saved: valid ? saved! : { value: "", revision }, previous: previous?.revision !== revision && typeof previous?.value === "string" && previous.value ? previous : undefined };
  });
  const [state, setState] = useState(() => ({ key, draft: initial.saved }));
  const draft: SavedDraft = state.key === key ? state.draft : memory.get(key) ?? { value: "", revision };
  const setDraft = (value: SavedDraft) => setState({ key, draft: value });
  const [previous, setPrevious] = useState(initial.previous);
  const [storageUnavailable, setStorageUnavailable] = useState(unavailableScopes.has(prefix));

  useEffect(() => {
    const current = memory.get(key);
    setDraft(current?.revision === revision ? current : { value: "", revision });
    setPrevious(undefined);
    try {
      const raw = localStorage.getItem(key);
      const latest = localStorage.getItem(latestKey);
      const stored = raw ? JSON.parse(raw) as SavedDraft : undefined;
      const storedLatest = latest ? JSON.parse(latest) as SavedDraft : undefined;
      const inMemory = memory.get(key);
      if (!inMemory && stored?.revision === revision && typeof stored.value === "string") {
        memory.set(key, stored);
        setDraft(stored);
      }
      const previous = memory.get(latestKey) ?? storedLatest;
      if (!memory.has(latestKey) && previous) memory.set(latestKey, previous);
      if (previous?.revision !== revision && previous?.value) setPrevious(previous);
    } catch {
      unavailableScopes.add(prefix);
      setStorageUnavailable(true);
    }
  }, [key, latestKey, prefix, revision]);

  function persist(next: SavedDraft) {
    memory.set(key, next);
    memory.set(latestKey, next);
    try {
      localStorage.setItem(key, JSON.stringify(next));
      localStorage.setItem(latestKey, JSON.stringify(next));
    } catch {
      unavailableScopes.add(prefix);
      setStorageUnavailable(true);
    }
    setDraft(next);
  }

  return {
    value: draft.value,
    setValue(value: string) { persist({ value, revision, ...(value === draft.attemptValue ? { attemptId: draft.attemptId, attemptValue: value } : {}) }); },
    attemptId() {
      const current = memory.get(key) ?? draft;
      if (current.attemptId && current.attemptValue === current.value) return current.attemptId;
      const id = newRequestId();
      persist({ ...current, attemptId: id, attemptValue: current.value });
      return id;
    },
    finishAttempt() { persist({ value: draft.value, revision }); },
    storageUnavailable,
    previous,
    restorePrevious() { if (previous) persist({ value: previous.value, revision }); setPrevious(undefined); },
    dismissPrevious() { setPrevious(undefined); persist(draft); },
  };
}
