import type {
  AuthResponse,
  ReadinessResponse,
  SessionHistoryResponse,
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

export async function runTeacherSession(
  requestPayload: TeacherSessionRequest,
  onProgress?: (status: string) => void,
): Promise<TeacherSessionResult> {
  return streamTeacherSession(requestPayload, onProgress);
}

async function streamTeacherSession(
  requestPayload: TeacherSessionRequest,
  onProgress?: (status: string) => void,
): Promise<TeacherSessionResult> {
  const controller = new AbortController();
  let didTimeout = false;
  const timeout = window.setTimeout(() => {
    didTimeout = true;
    controller.abort();
  }, REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE_URL}/teacher/session/stream`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestPayload),
      signal: controller.signal,
    });

    if (!response.ok) {
      const raw = await response.text();
      const parsed = tryParseJson(raw);
      const detail = parsed?.detail ?? raw ?? `Request failed with status ${response.status}`;
      throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), response.status, "HTTP", parsed);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new ApiError("No response body", null, "NETWORK");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = parseSSEBuffer(buffer);
      buffer = events.remaining;

      for (const evt of events.parsed) {
        if (evt.event === "progress" && onProgress) {
          const data = tryParseJson(evt.data);
          onProgress(data?.state ?? "processing");
        }
        if (evt.event === "result") {
          const data = tryParseJson(evt.data);
          if (data) return normalizeTeacherSessionResult(data as TeacherSessionResult);
        }
        if (evt.event === "error") {
          const data = tryParseJson(evt.data);
          throw new ApiError(data?.detail ?? "Teacher session failed", null, "HTTP", data);
        }
      }
    }

    // Stream ended without result — fall through to error
    throw new ApiError("Stream ended without result", null, "NETWORK");
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(didTimeout ? "Request timed out." : "Request cancelled.", null, didTimeout ? "TIMEOUT" : "ABORT");
    }
    throw new ApiError("Network error. Check API connectivity.", null, "NETWORK");
  } finally {
    window.clearTimeout(timeout);
  }
}

interface SSEEvent {
  event: string;
  data: string;
}

function parseSSEBuffer(buffer: string): { parsed: SSEEvent[]; remaining: string } {
  const parsed: SSEEvent[] = [];
  const blocks = buffer.split("\n\n");
  const remaining = blocks.pop() ?? "";

  for (const block of blocks) {
    if (!block.trim()) continue;
    let event = "message";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) data = line.slice(6);
    }
    if (event && data) parsed.push({ event, data });
  }

  return { parsed, remaining };
}

export function getSessionHistory(limit = 50): Promise<SessionHistoryResponse> {
  return request<SessionHistoryResponse>(`/teacher/session/history?limit=${limit}`, { method: "GET" });
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
