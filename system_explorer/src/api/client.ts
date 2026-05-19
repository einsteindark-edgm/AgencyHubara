// API client minimal: fetch wrapper que tira en errores HTTP y retorna
// `unknown` (Zod hace el parsing al schema concreto).
//
// En dev: Vite proxy mapea `/api/*` → http://localhost:8000 (FastAPI).
// En prod (docker): nginx hace el proxy (ver nginx.conf).

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiGet<_T = unknown>(path: string): Promise<unknown> {
  const res = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(
      `GET ${path} → ${res.status} ${res.statusText}`,
      res.status,
      body,
    );
  }
  return (await res.json()) as unknown;
}
