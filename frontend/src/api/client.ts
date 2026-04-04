import type {
  AuthResponse,
  ReadinessResponse,
  TeacherSessionRequest,
  TeacherSessionResult,
} from "../types/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/v1";
const REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_REQUEST_TIMEOUT_MS ?? "300000");

export interface AuthErrorPayload {
  code: string;
  message: string;
  field_errors?: Record<string, string>;
}

export class ApiError extends Error {
  status: number | null;
  code: "HTTP" | "TIMEOUT" | "NETWORK" | "ABORT";
  payload: unknown;

  constructor(message: string, status: number | null, code: "HTTP" | "TIMEOUT" | "NETWORK" | "ABORT", payload: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

interface RequestOptions {
  allowStatuses?: number[];
}

async function request<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
  const controller = new AbortController();
  let didTimeout = false;
  const timeout = window.setTimeout(() => {
    didTimeout = true;
    controller.abort();
  }, REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      signal: controller.signal,
    });

    const raw = await response.text();
    const parsed = raw ? tryParseJson(raw) : null;
    const allowedStatuses = new Set(options?.allowStatuses ?? []);

    if (!response.ok && !allowedStatuses.has(response.status)) {
      const authPayload = readAuthErrorPayload(parsed);
      if (authPayload) {
        throw new ApiError(authPayload.message, response.status, "HTTP", authPayload);
      }
      const detail =
        parsed && typeof parsed.detail === "string"
          ? parsed.detail
          : parsed && typeof parsed.detail === "object"
            ? JSON.stringify(parsed.detail)
            : raw;
      throw new ApiError(detail || `Request failed with status ${response.status}`, response.status, "HTTP", parsed);
    }

    return (parsed as T) ?? ({} as T);
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      if (didTimeout) {
        throw new ApiError("Request timed out.", null, "TIMEOUT");
      }
      throw new ApiError("Request cancelled.", null, "ABORT");
    }
    throw new ApiError("Network error. Check API connectivity.", null, "NETWORK");
  } finally {
    window.clearTimeout(timeout);
  }
}

function tryParseJson(raw: string): any | null {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function readAuthErrorPayload(parsed: any): AuthErrorPayload | null {
  if (!parsed || typeof parsed !== "object") {
    return null;
  }
  if (typeof parsed.code === "string" && typeof parsed.message === "string") {
    return parsed as AuthErrorPayload;
  }
  if (parsed.detail && typeof parsed.detail === "object") {
    const detail = parsed.detail;
    if (typeof detail.code === "string" && typeof detail.message === "string") {
      return detail as AuthErrorPayload;
    }
  }
  return null;
}

function normalizeTeacherSessionResult(result: TeacherSessionResult): TeacherSessionResult {
  return {
    ...result,
    citations: result.citations ?? [],
    current_stage: result.current_stage ?? null,
    plan: result.plan ?? null,
    lesson: result.lesson ?? null,
    retrieval_debug: result.retrieval_debug ?? null,
  };
}

export function getReadiness(): Promise<ReadinessResponse> {
  return request<ReadinessResponse>("/health/ready", { method: "GET" }, { allowStatuses: [503] });
}

export async function runTeacherSession(requestPayload: TeacherSessionRequest): Promise<TeacherSessionResult> {
  const result = await request<TeacherSessionResult>("/teacher/session", {
    method: "POST",
    body: JSON.stringify(requestPayload),
  });
  return normalizeTeacherSessionResult(result);
}

export function getCurrentLearner(): Promise<AuthResponse> {
  return request<AuthResponse>("/auth/me", { method: "GET" });
}

export function signup(firstName: string, lastName: string, email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ first_name: firstName, last_name: lastName, email, password }),
  });
}

export function login(email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function logout(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/auth/logout", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function requestPasswordReset(email: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function confirmPasswordReset(token: string, newPassword: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}
