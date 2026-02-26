import { ApiError } from "../../api/client";

export interface NormalizedApiError {
  message: string;
  timeout: boolean;
  cancelled: boolean;
}

export function normalizeApiError(error: unknown): NormalizedApiError {
  if (error instanceof ApiError) {
    if (error.code === "ABORT") {
      return { message: "Generation stopped.", timeout: false, cancelled: true };
    }
    return {
      message: error.message,
      timeout: error.code === "TIMEOUT",
      cancelled: false,
    };
  }
  return { message: "Unexpected client error.", timeout: false, cancelled: false };
}
