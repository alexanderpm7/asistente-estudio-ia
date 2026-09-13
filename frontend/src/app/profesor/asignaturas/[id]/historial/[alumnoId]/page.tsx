"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useAuth } from "../../../../../lib/auth-context";
import { NavBar } from "../../../../../../components/NavBar";
import { authHeader } from "../../../../../lib/api";

interface Mensaje {
  id: string;
  rol: "user" | "assistant";
  contenido: string;
  creado_en: string;
  uso_rag: boolean;
  n_iteraciones: number | null;
  latencia_ms: number | null;
}

interface HistorialResponse {
  mensajes: Mensaje[];
  has_more: boolean;
}

export default function HistorialPage() {
  const params = useParams<{ id: string; alumnoId: string }>();
  const { id: asignaturaId, alumnoId } = params;
  const { token } = useAuth();

  const [mensajes, setMensajes] = useState<Mensaje[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Vacío por defecto: el backend filtra la última semana en ese caso.
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");

  const buildUrl = useCallback(
    (before?: string) => {
      const qs = new URLSearchParams();
      if (desde) qs.set("desde", desde);
      if (hasta) qs.set("hasta", hasta);
      if (before) qs.set("before", before);
      const query = qs.toString();
      return `/api/asignaturas/${asignaturaId}/historial/${alumnoId}${query ? `?${query}` : ""}`;
    },
    [asignaturaId, alumnoId, desde, hasta],
  );

  useEffect(() => {
    if (!token) return;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(buildUrl(), { headers: authHeader(token) });
        if (!res.ok) throw new Error(`Error ${res.status}`);
        const data = (await res.json()) as HistorialResponse;
        setMensajes(data.mensajes);
        setHasMore(data.has_more);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Error al cargar el historial.");
      } finally {
        setLoading(false);
      }
    })();
  }, [token, buildUrl]);

  async function handleCargarMasAntiguos() {
    const masAntiguo = mensajes[0];
    if (!token || !masAntiguo || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await fetch(buildUrl(masAntiguo.id), { headers: authHeader(token) });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      const data = (await res.json()) as HistorialResponse;
      setMensajes((prev) => [...data.mensajes, ...prev]);
      setHasMore(data.has_more);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al cargar mensajes anteriores.");
    } finally {
      setLoadingMore(false);
    }
  }

  function handleLimpiarFiltro() {
    setDesde("");
    setHasta("");
  }

  const filtroActivo = desde !== "" || hasta !== "";

  return (
    <div className="min-h-screen bg-zinc-50">
      <NavBar
        title="Historial de conversación"
        backHref={`/profesor/asignaturas/${asignaturaId}`}
        backLabel="Asignatura"
      />

      <main className="mx-auto max-w-2xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-end gap-3 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
          <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
            Desde
            <input
              type="date"
              value={desde}
              onChange={(e) => setDesde(e.target.value)}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
            Hasta
            <input
              type="date"
              value={hasta}
              onChange={(e) => setHasta(e.target.value)}
              className="rounded-lg border border-zinc-300 px-3 py-1.5 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
            />
          </label>
          {filtroActivo && (
            <button
              onClick={handleLimpiarFiltro}
              className="rounded-lg px-3 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
            >
              Limpiar filtro
            </button>
          )}
          {!filtroActivo && (
            <p className="text-xs text-zinc-500">Mostrando la última semana por defecto.</p>
          )}
        </div>

        {loading && <p className="text-sm text-zinc-500">Cargando historial…</p>}

        {error && (
          <div className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        )}

        {!loading && !error && mensajes.length === 0 && (
          <p className="text-sm text-zinc-500">
            Este alumno no tiene mensajes en el rango seleccionado.
          </p>
        )}

        {!loading && hasMore && (
          <div className="mb-4 flex justify-center">
            <button
              onClick={() => void handleCargarMasAntiguos()}
              disabled={loadingMore}
              className="rounded-lg border border-zinc-300 px-4 py-1.5 text-sm text-zinc-600 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loadingMore ? "Cargando…" : "Cargar mensajes anteriores"}
            </button>
          </div>
        )}

        <div className="flex flex-col gap-4">
          {mensajes.map((m) => {
            const isUser = m.rol === "user";
            return (
              <div key={m.id} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                <div
                  className={[
                    "max-w-prose rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm",
                    isUser
                      ? "bg-zinc-900 text-white"
                      : "border border-zinc-200 bg-white text-zinc-800",
                  ].join(" ")}
                >
                  <p
                    className={[
                      "mb-1 text-xs font-semibold uppercase tracking-widest",
                      isUser ? "text-zinc-400" : "text-zinc-500",
                    ].join(" ")}
                  >
                    {isUser ? "Alumno" : "Asistente"}
                  </p>
                  <p className="whitespace-pre-wrap">{m.contenido}</p>
                  <div
                    className={[
                      "mt-2 flex gap-3 text-xs",
                      isUser ? "text-zinc-400" : "text-zinc-500",
                    ].join(" ")}
                  >
                    <span>{new Date(m.creado_en).toLocaleString("es-ES")}</span>
                    {!isUser && m.uso_rag && (
                      <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-zinc-600">RAG</span>
                    )}
                    {!isUser && m.latencia_ms != null && (
                      <span>{(m.latencia_ms / 1000).toFixed(1)} s</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </main>
    </div>
  );
}
