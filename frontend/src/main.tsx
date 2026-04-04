import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";

import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { queryClient } from "./lib/queryClient";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary
      fallback={(error) => (
        <div className="flex min-h-screen items-center justify-center bg-[#F8FAFC] p-6 text-slate-900">
          <div className="w-full max-w-lg rounded-[2rem] border border-[color:var(--line-soft)] bg-white/90 p-8 shadow-[var(--shadow-large)] backdrop-blur">
            <h1 className="text-2xl font-bold tracking-tight text-[color:var(--ink-strong)]">
              Frontend error
            </h1>
            <p className="mt-3 text-sm leading-relaxed text-[color:var(--ink-muted)]">
              The page hit a rendering error. Reload and try again.
            </p>
            {import.meta.env.DEV && error ? (
              <pre className="mt-4 overflow-x-auto rounded-[1.4rem] bg-slate-950 px-4 py-3 text-xs text-slate-100">
                {error.stack || error.message}
              </pre>
            ) : null}
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-6 rounded-full bg-[color:var(--accent-strong)] px-4 py-2 text-sm font-semibold text-white hover:brightness-95"
            >
              Reload
            </button>
          </div>
        </div>
      )}
    >
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
