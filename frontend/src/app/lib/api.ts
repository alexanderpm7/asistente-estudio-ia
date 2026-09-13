/** Helpers de fetch compartidos por las páginas del frontend. */

/** Devuelve la cabecera Authorization lista para usar en `headers`. */
export function authHeader(token: string | null): { Authorization: string } {
  return { Authorization: `Bearer ${token ?? ""}` };
}

/** Extrae `detail` de una respuesta de error o devuelve un fallback. */
export async function extraerDetalleError(res: Response, fallback?: string): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    return body.detail ?? fallback ?? `Error ${res.status}`;
  } catch {
    return fallback ?? `Error ${res.status}`;
  }
}
