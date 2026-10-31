import {
  ConfigResponse,
  FixtureInfo,
  PipelineRunRecord,
  SystemStats,
} from "./types";

/**
 * Resolves the base URL for the backend API.
 * - In Vercel server runtime (functions/SSR), reads `process.env.API_URL` injected by Vercel service binding.
 * - In client/browser, checks `process.env.NEXT_PUBLIC_API_URL`.
 * - When in browser under same-origin rewrites on Vercel, returns "" (relative path).
 * - Defaults to http://localhost:8000 for local development.
 */
export function getApiBaseUrl(): string {
  // 1. Vercel internal service binding (injected into server runtime functions)
  if (process.env.API_URL) {
    return process.env.API_URL.replace(/\/$/, "");
  }

  // 2. Client-side explicit override if provided
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL.replace(/\/$/, "");
  }

  // 3. Browser runtime environment
  if (typeof window !== "undefined") {
    // If running on local Next.js dev server (port 3000), target local FastAPI (port 8000)
    if (window.location.hostname === "localhost" && window.location.port === "3000") {
      return "http://localhost:8000";
    }
    // Under Vercel public routing rewrites, requests route through same domain
    return "";
  }

  // 4. Default fallback for local build / server-side rendering
  return "http://localhost:8000";
}

/**
 * Resolves an API path against the active base URL.
 * Supports internal service binding URLs, relative URLs, or absolute URLs.
 */
export function resolveApiUrl(path: string): string {
  const base = getApiBaseUrl();
  const cleanPath = path.startsWith("/") ? path : `/${path}`;

  if (!base) {
    return cleanPath;
  }

  try {
    return new URL(cleanPath, base).toString();
  } catch {
    return `${base}${cleanPath}`;
  }
}

export async function fetchHealth(): Promise<{ status: string }> {
  const res = await fetch(resolveApiUrl("/health"), { cache: "no-store" });
  if (!res.ok) throw new Error("API not healthy");
  return res.json();
}

export async function fetchStats(): Promise<SystemStats> {
  const res = await fetch(resolveApiUrl("/api/reports/stats"), { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to load stats");
  return res.json();
}

export async function fetchReports(): Promise<PipelineRunRecord[]> {
  const res = await fetch(resolveApiUrl("/api/reports"), { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to load reports");
  return res.json();
}

export async function fetchReport(runId: string): Promise<PipelineRunRecord> {
  const res = await fetch(resolveApiUrl(`/api/reports/${runId}`), { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load report ${runId}`);
  return res.json();
}

export async function deleteReport(runId: string): Promise<void> {
  const res = await fetch(resolveApiUrl(`/api/reports/${runId}`), {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete report ${runId}`);
}

export async function fetchConfig(): Promise<ConfigResponse> {
  const res = await fetch(resolveApiUrl("/api/config"), { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to load configuration");
  return res.json();
}

export async function updateConfig(updates: Partial<ConfigResponse>): Promise<ConfigResponse> {
  const res = await fetch(resolveApiUrl("/api/config"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error("Failed to update configuration");
  return res.json();
}

export async function fetchFixtures(): Promise<FixtureInfo[]> {
  const res = await fetch(resolveApiUrl("/api/upload/fixtures"), { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to load fixtures");
  return res.json();
}

export async function triggerPipelineRun(params: {
  fixture_name?: string;
  plan_content?: any;
  llm_provider?: string;
  dry_run?: boolean;
}): Promise<PipelineRunRecord> {
  const res = await fetch(resolveApiUrl("/api/pipeline/run"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      fixture_name: params.fixture_name,
      plan_content: params.plan_content,
      llm_provider: params.llm_provider || "mock",
      dry_run: params.dry_run ?? true,
    }),
  });
  if (!res.ok) throw new Error("Failed to trigger pipeline");
  return res.json();
}

export async function uploadPlanFile(file: File): Promise<any> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(resolveApiUrl("/api/upload/plan"), {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(err.detail || "Failed to upload plan JSON");
  }
  return res.json();
}
