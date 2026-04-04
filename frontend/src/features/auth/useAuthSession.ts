import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  ApiError,
  type AuthErrorPayload,
  confirmPasswordReset,
  getCurrentLearner,
  login,
  logout,
  requestPasswordReset,
  signup,
} from "../../api/client";
import { queryClient } from "../../lib/queryClient";
import type { AuthLearner } from "../../types/api";

type AuthMode = "login" | "signup" | "forgot" | "reset";

export interface AuthSessionState {
  learner: AuthLearner | null;
  passwordResetEnabled: boolean;
  authUnavailableReason: string | null;
  mode: AuthMode;
  setMode: (mode: AuthMode) => void;
  loading: boolean;
  error: string | null;
  fieldErrors: Record<string, string>;
  login: (email: string, password: string) => Promise<void>;
  signup: (firstName: string, lastName: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  requestReset: (email: string) => Promise<void>;
  confirmReset: (token: string, newPassword: string) => Promise<void>;
  resetNotice: string | null;
  consumeResetToken: () => string | null;
}

function readResetToken(): string | null {
  const params = new URLSearchParams(window.location.search);
  return params.get("reset_token");
}

export function useAuthSession(): AuthSessionState {
  const passwordResetEnabled = import.meta.env.VITE_AUTH_RESET_ENABLED !== "false";
  const authUnavailableReason = import.meta.env.DEV
    ? "Open the learner app through the main web origin to sign up or sign in. Direct Vite auth is intentionally disabled."
    : null;
  const [mode, setModeState] = useState<AuthMode>(() =>
    passwordResetEnabled && readResetToken() ? "reset" : "login",
  );
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [resetNotice, setResetNotice] = useState<string | null>(null);
  const setMode = (nextMode: AuthMode) => {
    setError(null);
    setFieldErrors({});
    if (nextMode !== "login") {
      setResetNotice(null);
    }
    setModeState(nextMode);
  };

  const currentLearnerQuery = useQuery({
    queryKey: ["auth", "me"],
    queryFn: getCurrentLearner,
    retry: false,
  });

  const loginMutation = useMutation({ mutationFn: ({ email, password }: { email: string; password: string }) => login(email, password) });
  const signupMutation = useMutation({
    mutationFn: ({
      firstName,
      lastName,
      email,
      password,
    }: {
      firstName: string;
      lastName: string;
      email: string;
      password: string;
    }) => signup(firstName, lastName, email, password),
  });
  const logoutMutation = useMutation({ mutationFn: logout });
  const requestResetMutation = useMutation({ mutationFn: ({ email }: { email: string }) => requestPasswordReset(email) });
  const confirmResetMutation = useMutation({
    mutationFn: ({ token, newPassword }: { token: string; newPassword: string }) =>
      confirmPasswordReset(token, newPassword),
  });

  const loading =
    currentLearnerQuery.isPending ||
    loginMutation.isPending ||
    signupMutation.isPending ||
    logoutMutation.isPending ||
    requestResetMutation.isPending ||
    confirmResetMutation.isPending;

  const learner = currentLearnerQuery.data?.learner ?? null;
  const authQueryKey = ["auth", "me"] as const;

  const handleAuthResult = async (runner: () => Promise<void>) => {
    setError(null);
    setFieldErrors({});
    if (authUnavailableReason) {
      setError(authUnavailableReason);
      return;
    }
    try {
      await runner();
      await currentLearnerQuery.refetch();
    } catch (authError) {
      const payload = authError instanceof ApiError ? (authError.payload as AuthErrorPayload | null) : null;
      if (payload?.field_errors) {
        setFieldErrors(payload.field_errors);
      }
      const message = authError instanceof Error ? authError.message : "Authentication failed.";
      setError(message);
    }
  };

  const consumeResetToken = () => {
    const token = readResetToken();
    if (!token) {
      return null;
    }
    const url = new URL(window.location.href);
    url.searchParams.delete("reset_token");
    window.history.replaceState({}, "", url.toString());
    return token;
  };

  return useMemo(
    () => ({
      learner,
      passwordResetEnabled,
      authUnavailableReason,
      mode,
      setMode,
      loading,
      error,
      fieldErrors,
      login: async (email: string, password: string) => {
        await handleAuthResult(async () => {
          await loginMutation.mutateAsync({ email, password });
          queryClient.invalidateQueries({ queryKey: authQueryKey });
          setMode("login");
        });
      },
      signup: async (firstName: string, lastName: string, email: string, password: string) => {
        await handleAuthResult(async () => {
          await signupMutation.mutateAsync({ firstName, lastName, email, password });
          queryClient.invalidateQueries({ queryKey: authQueryKey });
          setMode("login");
        });
      },
      logout: async () => {
        setError(null);
        setFieldErrors({});
        try {
          await logoutMutation.mutateAsync();
          queryClient.setQueryData(authQueryKey, null);
          setMode("login");
        } catch (authError) {
          const message = authError instanceof Error ? authError.message : "Sign out failed.";
          setError(message);
        }
      },
      requestReset: async (email: string) => {
        if (!passwordResetEnabled) {
          setError("Password reset is not available in this deployment.");
          return;
        }
        setError(null);
        setFieldErrors({});
        try {
          await requestResetMutation.mutateAsync({ email });
          setResetNotice("If the account exists, a reset link has been sent.");
          setMode("login");
        } catch (authError) {
          const payload = authError instanceof ApiError ? (authError.payload as AuthErrorPayload | null) : null;
          if (payload?.field_errors) {
            setFieldErrors(payload.field_errors);
          }
          const message = authError instanceof Error ? authError.message : "Password reset request failed.";
          setError(message);
        }
      },
      confirmReset: async (token: string, newPassword: string) => {
        if (!passwordResetEnabled) {
          setError("Password reset is not available in this deployment.");
          return;
        }
        setError(null);
        setFieldErrors({});
        try {
          await confirmResetMutation.mutateAsync({ token, newPassword });
          setResetNotice("Password updated. Sign in with the new password.");
          setMode("login");
        } catch (authError) {
          const payload = authError instanceof ApiError ? (authError.payload as AuthErrorPayload | null) : null;
          if (payload?.field_errors) {
            setFieldErrors(payload.field_errors);
          }
          const message = authError instanceof Error ? authError.message : "Password reset failed.";
          setError(message);
        }
      },
      resetNotice,
      consumeResetToken,
    }),
    [
      learner,
      passwordResetEnabled,
      authUnavailableReason,
      mode,
      loading,
      error,
      fieldErrors,
      resetNotice,
      currentLearnerQuery,
      loginMutation,
      signupMutation,
      logoutMutation,
      requestResetMutation,
      confirmResetMutation,
    ],
  );
}
