import { apiGet } from "./client";
import type { DashboardPayload, SourcesPayload, StatusPayload } from "./types";

export function fetchStatus(signal?: AbortSignal): Promise<StatusPayload> {
  return apiGet<StatusPayload>("/status", undefined, signal);
}

export function fetchDashboard(signal?: AbortSignal): Promise<DashboardPayload> {
  return apiGet<DashboardPayload>("/dashboard", undefined, signal);
}

export function fetchSources(signal?: AbortSignal): Promise<SourcesPayload> {
  return apiGet<SourcesPayload>("/sources", undefined, signal);
}
