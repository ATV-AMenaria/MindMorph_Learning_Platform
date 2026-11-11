"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AUTH_CHANGED, clearAuth, readAuth, saveAuth, type AuthSession } from "./auth";

export function useUser() {
  const [userId, setUserId] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const client = useQueryClient();

  useEffect(() => {
    const refresh = () => {
      const session = readAuth();
      setUserId(session?.userId ?? null);
      setEmail(session?.email ?? null);
      setReady(true);
      if (!session) {
        void client.cancelQueries();
        client.clear();
      }
    };
    refresh();
    window.addEventListener(AUTH_CHANGED, refresh);
    return () => window.removeEventListener(AUTH_CHANGED, refresh);
  }, [client]);

  const signIn = (session: AuthSession) => {
    void client.cancelQueries();
    client.clear();
    saveAuth(session);
  };

  const signOut = () => {
    void client.cancelQueries();
    client.clear();
    clearAuth();
  };

  return { userId, email, ready, signIn, signOut };
}
