import type { Citation } from "../../types/api";

export interface FeedMessage {
  id: string;
  role: "assistant" | "user" | "system" | "error";
  title?: string;
  content: string;
  citations?: Citation[];
}
