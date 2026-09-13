"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "../../lib/auth-context";
import { NavBar } from "../../../components/NavBar";
import { authHeader, extraerDetalleError } from "../../lib/api";

interface Asignatura {
  id: string;
  nombre: string;
  descripcion: string | null;
  profesor_id: string;
}

export default function ProfesorAsignaturasPage() {
  const { token } = useAuth();

  const [asignaturas, setAsignaturas] = useState<Asignatura[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [creando, setCreando] = useState(false);
  const [nuevoNombre, setNuevoNombre] = useState("");
  const [nuevaDesc, setNuevaDesc] = useState("");
  const [creandoLoading, setCreandoLoading] = useState(false);

  const [editandoId, setEditandoId] = useState<string | null>(null);
  const [editNombre, setEditNombre] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editLoading, setEditLoading] = useState(false);

  function fetchAsignaturas(): Promise<void> {
    return fetch("/api/asignaturas", { headers: authHeader(token) })
      .then((res) => {
        if (!res.ok) throw new Error(`Error ${res.status}`);
        return res.json() as Promise<Asignatura[]>;
      })
      .then((data) => setAsignaturas(data))
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Error al cargar asignaturas.");
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (token) void fetchAsignaturas();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function handleCrear(e: { preventDefault(): void }) {
    e.preventDefault();
    setCreandoLoading(true);
    try {
      const res = await fetch("/api/asignaturas", {
        method: "POST",
        headers: { ...authHeader(token), "Content-Type": "application/json" },
        body: JSON.stringify({ nombre: nuevoNombre, descripcion: nuevaDesc || null }),
      });
      if (!res.ok) throw new Error(await extraerDetalleError(res));
      setNuevoNombre("");
      setNuevaDesc("");
      setCreando(false);
      await fetchAsignaturas();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al crear asignatura.");
    } finally {
      setCreandoLoading(false);
    }
  }

  function startEdit(a: Asignatura) {
    setEditandoId(a.id);
    setEditNombre(a.nombre);
    setEditDesc(a.descripcion ?? "");
  }

  async function handleEditar(e: { preventDefault(): void }) {
    e.preventDefault();
    if (!editandoId) return;
    setEditLoading(true);
    try {
      const res = await fetch(`/api/asignaturas/${editandoId}`, {
        method: "PUT",
        headers: { ...authHeader(token), "Content-Type": "application/json" },
        body: JSON.stringify({ nombre: editNombre, descripcion: editDesc || null }),
      });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      setEditandoId(null);
      await fetchAsignaturas();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al editar asignatura.");
    } finally {
      setEditLoading(false);
    }
  }

  async function handleEliminar(id: string, nombre: string) {
    if (!confirm(`¿Eliminar la asignatura "${nombre}"? Esta acción no se puede deshacer.`)) return;
    try {
      const res = await fetch(`/api/asignaturas/${id}`, {
        method: "DELETE",
        headers: authHeader(token),
      });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      await fetchAsignaturas();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al eliminar asignatura.");
    }
  }

  return (
    <div className="min-h-screen bg-zinc-50">
      <NavBar title="Panel del profesor" />

      <main className="mx-auto max-w-3xl px-4 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-zinc-900">Mis asignaturas</h2>
          <button
            onClick={() => setCreando(!creando)}
            className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700"
          >
            {creando ? "Cancelar" : "Nueva asignatura"}
          </button>
        </div>

        {creando && (
          <form
            onSubmit={handleCrear}
            className="mb-6 rounded-xl border border-zinc-200 bg-white p-5 shadow-sm"
          >
            <h3 className="mb-4 text-sm font-semibold text-zinc-800">Crear asignatura</h3>
            <div className="flex flex-col gap-3">
              <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
                Nombre *
                <input
                  type="text"
                  value={nuevoNombre}
                  onChange={(e) => setNuevoNombre(e.target.value)}
                  required
                  maxLength={255}
                  className="rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
                  placeholder="Matemáticas I"
                />
              </label>
              <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
                Descripción
                <textarea
                  value={nuevaDesc}
                  onChange={(e) => setNuevaDesc(e.target.value)}
                  rows={2}
                  className="rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200 resize-none"
                  placeholder="Descripción opcional"
                />
              </label>
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setCreando(false)}
                  className="rounded-lg px-4 py-2 text-sm text-zinc-600 transition-colors hover:bg-zinc-100"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={creandoLoading}
                  className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {creandoLoading ? "Creando…" : "Crear"}
                </button>
              </div>
            </div>
          </form>
        )}

        {error && (
          <div className="mb-4 flex items-start justify-between rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
            <span>{error}</span>
            <button
              onClick={() => setError(null)}
              className="ml-4 font-semibold text-red-500 transition-colors hover:text-red-700"
            >
              ✕
            </button>
          </div>
        )}

        {loading ? (
          <p className="text-sm text-zinc-500">Cargando…</p>
        ) : asignaturas.length === 0 ? (
          <p className="text-sm text-zinc-500">No tienes ninguna asignatura todavía.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {asignaturas.map((a) => (
              <li key={a.id} className="rounded-xl border border-zinc-200 bg-white shadow-sm">
                {editandoId === a.id ? (
                  <form onSubmit={handleEditar} className="p-5">
                    <div className="flex flex-col gap-3">
                      <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
                        Nombre *
                        <input
                          type="text"
                          value={editNombre}
                          onChange={(e) => setEditNombre(e.target.value)}
                          required
                          maxLength={255}
                          className="rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
                        />
                      </label>
                      <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
                        Descripción
                        <textarea
                          value={editDesc}
                          onChange={(e) => setEditDesc(e.target.value)}
                          rows={2}
                          className="rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200 resize-none"
                        />
                      </label>
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setEditandoId(null)}
                          className="rounded-lg px-4 py-2 text-sm text-zinc-600 transition-colors hover:bg-zinc-100"
                        >
                          Cancelar
                        </button>
                        <button
                          type="submit"
                          disabled={editLoading}
                          className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {editLoading ? "Guardando…" : "Guardar"}
                        </button>
                      </div>
                    </div>
                  </form>
                ) : (
                  <div className="flex items-start justify-between p-5">
                    <div className="flex-1">
                      <Link
                        href={`/profesor/asignaturas/${a.id}`}
                        className="rounded text-base font-medium text-zinc-900 transition-colors hover:text-zinc-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
                      >
                        {a.nombre}
                      </Link>
                      {a.descripcion && (
                        <p className="mt-1 text-sm text-zinc-500">{a.descripcion}</p>
                      )}
                    </div>
                    <div className="ml-4 flex shrink-0 gap-2">
                      <button
                        onClick={() => startEdit(a)}
                        className="rounded-lg px-3 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
                      >
                        Editar
                      </button>
                      <button
                        onClick={() => void handleEliminar(a.id, a.nombre)}
                        className="rounded-lg px-3 py-1.5 text-sm text-red-500 transition-colors hover:bg-red-50 hover:text-red-700"
                      >
                        Eliminar
                      </button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}
