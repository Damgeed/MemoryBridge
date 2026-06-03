export interface User {
  id: string;
  email: string;
  name?: string;
  avatar_url?: string;
  plan: "free" | "pro" | "enterprise";
}

export async function getCurrentUser(): Promise<User | null> {
  try {
    const { fetcher } = await import("./fetcher");
    return await fetcher<User>("/api/v1/users/me");
  } catch {
    return null;
  }
}

export async function login(token: string): Promise<void> {
  localStorage.setItem("token", token);
}

export async function logout(): Promise<void> {
  localStorage.removeItem("token");
}

export function isAuthenticated(): boolean {
  if (typeof window === "undefined") return false;
  return !!localStorage.getItem("token");
}
