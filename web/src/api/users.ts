import { apiGet } from "./client";
import type { UserProfile, UsersResponse } from "./types";

export async function fetchUsers(signal?: AbortSignal): Promise<UserProfile[]> {
  const res = await apiGet<UsersResponse>("/users", undefined, signal);
  return res.users;
}
