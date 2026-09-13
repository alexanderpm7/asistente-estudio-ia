"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "../../../lib/auth-context";
import { NavBar } from "../../../../components/NavBar";
import { authHeader } from "../../../lib/api";

interface Asignatura {
  id: string;
  nombre: string;
  descripcion: string | null;
}

export default function AlumnoAsignaturasPage() {
  const { token } = useAuth();

  const [asignaturas, setAsignaturas] = useState<Asignatura[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    void (async () => {
      try {
        const res = await fetch("/api/asignaturas", { headers: authHeader(token) });
        if (!res.ok) throw new Error(`Error ${res.status}`);
        setAsignaturas((await res.json()) as Asignatura[]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Error al cargar las asignaturas.");
      } finally {
        setLoading(false);
      }
    })();
  }, [token]);

  return (
    <div className="min-h-screen bg-zinc-50">
      <NavBar title="Mis asignaturas" />

      <main className="mx-auto max-w-3xl px-4 py-8">
        {loading && <p className="text-sm text-zinc-500">Cargando…</p>}

        {error && (
          <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        )}

        {!loading && !error && asignaturas.length === 0 && (
          <p className="text-sm text-zinc-500">
            No estás matriculado en ninguna asignatura todavía.
          </p>
        )}

        <ul className="flex flex-col gap-3">
          {asignaturas.map((a) => (
            <li key={a.id} className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
              <Link
                href={`/chat/${a.id}`}
                className="rounded text-base font-medium text-zinc-900 transition-colors hover:text-zinc-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
              >
                {a.nombre}
              </Link>
              {a.descripcion && (
                <p className="mt-1 text-sm text-zinc-500">{a.descripcion}</p>
              )}
              <div className="mt-3 flex gap-2">
                <Link
                  href={`/chat/${a.id}`}
                  className="inline-block rounded-lg bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
                >
                  Abrir chat
                </Link>
                <Link
                  href={`/alumno/asignaturas/${a.id}/repaso`}
                  className="inline-block rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
                >
                  Modo repaso
                </Link>
              </div>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
