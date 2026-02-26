import type {
  ChatResponse,
  HealthResponse,
  LessonCurrentResponse,
  NextResponse,
  StartResponse,
} from "../types/api";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/v1";
const REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_REQUEST_TIMEOUT_MS ?? "90000");

export class ApiError extends Error {
  status: number | null;
  code: "HTTP" | "TIMEOUT" | "NETWORK" | "ABORT";

  constructor(message: string, status: number | null, code: "HTTP" | "TIMEOUT" | "NETWORK" | "ABORT") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  let didTimeout = false;
  const timeout = window.setTimeout(() => {
    didTimeout = true;
    controller.abort();
  }, REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      signal: controller.signal,
    });

    const raw = await response.text();
    const parsed = raw ? tryParseJson(raw) : null;

    if (!response.ok) {
      const detail = parsed && typeof parsed.detail === "string" ? parsed.detail : raw;
      throw new ApiError(
        detail || `Request failed with status ${response.status}`,
        response.status,
        "HTTP",
      );
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

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { method: "GET" });
}

export function startSession(learnerId: string): Promise<StartResponse> {
  return request<StartResponse>("/start", {
    method: "POST",
    body: JSON.stringify({ learner_id: learnerId }),
  });
}

export function nextSection(learnerId: string, force = false): Promise<NextResponse> {
  return request<NextResponse>("/next", {
    method: "POST",
    body: JSON.stringify({ learner_id: learnerId, force }),
  });
}

export function getCurrentLesson(learnerId: string): Promise<LessonCurrentResponse> {
  const params = new URLSearchParams({ learner_id: learnerId });
  return request<LessonCurrentResponse>(`/lesson/current?${params.toString()}`, {
    method: "GET",
  });
}

export function sendChat(
  learnerId: string,
  message: string,
  moduleId: string | null,
  sectionId: string | null,
): Promise<ChatResponse> {
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({
      learner_id: learnerId,
      message,
      context: {
        current_module_id: moduleId,
        current_section_id: sectionId,
      },
      mode: "tutor",
    }),
  });
}
