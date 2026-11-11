// Only server-issued credentials establish identity. Legacy email-only IDs are
// deliberately ignored; saved drafts and legacy learning records are retained.
export interface AuthSession {
  accessToken: string;
  userId: string;
  email: string;
}

const KEY = "mindmorph.auth";
export const AUTH_CHANGED = "mindmorph.auth.changed";
let memorySession: AuthSession | null = null;

function valid(value: unknown): value is AuthSession {
  if (!value || typeof value !== "object") return false;
  const session = value as Partial<AuthSession>;
  return typeof session.accessToken === "string" && !!session.accessToken.trim()
    && typeof session.userId === "string" && !!session.userId.trim()
    && typeof session.email === "string" && !!session.email.trim();
}

export function readAuth(): AuthSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return memorySession;
    const value: unknown = JSON.parse(raw);
    return valid(value) ? value : null;
  } catch {
    return memorySession;
  }
}

export function saveAuth(session: AuthSession) {
  if (!valid(session)) throw new Error("The server returned an invalid sign-in response.");
  memorySession = session;
  try { sessionStorage.setItem(KEY, JSON.stringify(session)); } catch { /* Keep this tab signed in when storage is unavailable. */ }
  window.dispatchEvent(new Event(AUTH_CHANGED));
}

export function clearAuth() {
  memorySession = null;
  try { sessionStorage.removeItem(KEY); } catch { /* No persisted credentials to remove. */ }
  window.dispatchEvent(new Event(AUTH_CHANGED));
}
