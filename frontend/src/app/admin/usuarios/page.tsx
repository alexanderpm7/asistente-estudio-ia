"use client";

import { useEffect, useState } from "react";
import { useAuth } from "../../lib/auth-context";
import { NavBar } from "../../../components/NavBar";
import { authHeader, extraerDetalleError } from "../../lib/api";

type Rol = "alumno" | "profesor" | "admin";

interface Usuario {
  id: string;
  email: string;
  nombre: string;
  rol: Rol;
  activo: boolean;
  creado_en: string;
}

interface ImportarError {
  fila: number;
  email: string | null;
  error: string;
}

interface ImportarResultado {
  creados: number;
  errores: ImportarError[];
}

const ROLES: Rol[] = ["alumno", "profesor", "admin"];

export default function AdminUsuariosPage() {
  const { token, user } = useAuth();

  const [usuarios, setUsuarios] = useState<Usuario[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filtroRol, setFiltroRol] = useState<Rol | "">("");

  const [creando, setCreando] = useState(false);
  const [nuevoEmail, setNuevoEmail] = useState("");
  const [nuevoNombre, setNuevoNombre] = useState("");
  const [nuevoRol, setNuevoRol] = useState<Rol>("alumno");
  const [creandoLoading, setCreandoLoading] = useState(false);

  const [editandoId, setEditandoId] = useState<string | null>(null);
  const [editEmail, setEditEmail] = useState("");
  const [editRol, setEditRol] = useState<Rol>("alumno");
  const [editActivo, setEditActivo] = useState(true);
  const [editLoading, setEditLoading] = useState(false);

  const [importando, setImportando] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importLoading, setImportLoading] = useState(false);
  const [importResultado, setImportResultado] = useState<ImportarResultado | null>(null);

  function fetchUsuarios(rol: Rol | ""): Promise<void> {
    const url = rol ? `/api/admin/usuarios?rol=${rol}` : "/api/admin/usuarios";
    return fetch(url, { headers: authHeader(token) })
      .then((res) => {
        if (!res.ok) throw new Error(`Error ${res.status}`);
        return res.json() as Promise<Usuario[]>;
      })
      .then((data) => setUsuarios(data))
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Error al cargar usuarios.");
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (token) void fetchUsuarios(filtroRol);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, filtroRol]);

  async function handleCrear(e: { preventDefault(): void }) {
    e.preventDefault();
    setCreandoLoading(true);
    try {
      const res = await fetch("/api/admin/usuarios", {
        method: "POST",
        headers: { ...authHeader(token), "Content-Type": "application/json" },
        body: JSON.stringify({ email: nuevoEmail, nombre: nuevoNombre, rol: nuevoRol }),
      });
      if (!res.ok) {
        throw new Error(await extraerDetalleError(res));
      }
      setNuevoEmail("");
      setNuevoNombre("");
      setNuevoRol("alumno");
      setCreando(false);
      await fetchUsuarios(filtroRol);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al crear el usuario.");
    } finally {
      setCreandoLoading(false);
    }
  }

  function startEdit(u: Usuario) {
    setEditandoId(u.id);
    setEditEmail(u.email);
    setEditRol(u.rol);
    setEditActivo(u.activo);
  }

  async function handleEditar(e: { preventDefault(): void }) {
    e.preventDefault();
    if (!editandoId) return;
    setEditLoading(true);
    try {
      const res = await fetch(`/api/admin/usuarios/${editandoId}`, {
        method: "PATCH",
        headers: { ...authHeader(token), "Content-Type": "application/json" },
        body: JSON.stringify({ email: editEmail, rol: editRol, activo: editActivo }),
      });
      if (!res.ok) {
        throw new Error(await extraerDetalleError(res));
      }
      setEditandoId(null);
      await fetchUsuarios(filtroRol);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al editar el usuario.");
    } finally {
      setEditLoading(false);
    }
  }

  async function handleEliminar(id: string, email: string) {
    if (!confirm(`¿Eliminar el usuario "${email}"? Esta acción no se puede deshacer.`)) return;
    try {
      const res = await fetch(`/api/admin/usuarios/${id}`, {
        method: "DELETE",
        headers: authHeader(token),
      });
      if (!res.ok) {
        throw new Error(await extraerDetalleError(res));
      }
      await fetchUsuarios(filtroRol);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al eliminar el usuario.");
    }
  }

  async function handleImportar(e: { preventDefault(): void }) {
    e.preventDefault();
    if (!importFile) return;
    setImportLoading(true);
    setImportResultado(null);
    try {
      const formData = new FormData();
      formData.append("file", importFile);
      const res = await fetch("/api/admin/usuarios/importar", {
        method: "POST",
        headers: authHeader(token),
        body: formData,
      });
      if (!res.ok) {
        throw new Error(await extraerDetalleError(res));
      }
      const resultado = (await res.json()) as ImportarResultado;
      setImportResultado(resultado);
      setImportFile(null);
      await fetchUsuarios(filtroRol);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al importar el CSV.");
    } finally {
      setImportLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-50">
      <NavBar title="Panel de administración" />

      <main className="mx-auto max-w-4xl px-4 py-8">
        <div className="mb-6 flex items-center justify-between gap-4">
          <h2 className="text-lg font-semibold text-zinc-900">Usuarios</h2>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-zinc-600">
              Rol
              <select
                value={filtroRol}
                onChange={(e) => setFiltroRol(e.target.value as Rol | "")}
                className="rounded-lg border border-zinc-300 px-2 py-1.5 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
              >
                <option value="">Todos</option>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => {
                setImportando(!importando);
                setImportResultado(null);
              }}
              className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100"
            >
              {importando ? "Cancelar" : "Importar CSV"}
            </button>
            <button
              onClick={() => setCreando(!creando)}
              className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700"
            >
              {creando ? "Cancelar" : "Nuevo usuario"}
            </button>
          </div>
        </div>

        {importando && (
          <form
            onSubmit={handleImportar}
            className="mb-6 rounded-xl border border-zinc-200 bg-white p-5 shadow-sm"
          >
            <h3 className="mb-4 text-sm font-semibold text-zinc-800">Importar usuarios por CSV</h3>
            <p className="mb-3 text-xs text-zinc-500">
              El fichero debe tener las columnas <code>email</code>, <code>nombre</code> y{" "}
              <code>rol</code> (alumno, profesor o admin). Cada usuario creado recibe un correo
              para establecer su contraseña.
            </p>
            <div className="flex flex-col gap-3">
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
                required
                className="text-sm text-zinc-700 file:mr-3 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-zinc-700 hover:file:bg-zinc-200"
              />
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setImportando(false)}
                  className="rounded-lg px-4 py-2 text-sm text-zinc-600 transition-colors hover:bg-zinc-100"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={importLoading || !importFile}
                  className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {importLoading ? "Importando…" : "Importar"}
                </button>
              </div>
            </div>
          </form>
        )}

        {importResultado && (
          <div className="mb-6 rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
            <p className="text-sm font-medium text-zinc-800">
              {importResultado.creados} usuario(s) creado(s)
              {importResultado.errores.length > 0 &&
                `, ${importResultado.errores.length} fila(s) con error`}
              .
            </p>
            {importResultado.errores.length > 0 && (
              <ul className="mt-3 flex flex-col gap-1 text-sm text-red-700">
                {importResultado.errores.map((e) => (
                  <li key={e.fila}>
                    Fila {e.fila}
                    {e.email ? ` (${e.email})` : ""}: {e.error}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {creando && (
          <form
            onSubmit={handleCrear}
            className="mb-6 rounded-xl border border-zinc-200 bg-white p-5 shadow-sm"
          >
            <h3 className="mb-4 text-sm font-semibold text-zinc-800">Crear usuario</h3>
            <div className="flex flex-col gap-3">
              <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
                Email *
                <input
                  type="email"
                  value={nuevoEmail}
                  onChange={(e) => setNuevoEmail(e.target.value)}
                  required
                  className="rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
                  placeholder="usuario@ejemplo.com"
                />
              </label>
              <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
                Nombre *
                <input
                  type="text"
                  value={nuevoNombre}
                  onChange={(e) => setNuevoNombre(e.target.value)}
                  required
                  maxLength={255}
                  className="rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
                />
              </label>
              <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
                Rol *
                <select
                  value={nuevoRol}
                  onChange={(e) => setNuevoRol(e.target.value as Rol)}
                  className="rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </label>
              <p className="text-xs text-zinc-500">
                Se genera una contraseña inicial que no se muestra en claro; el usuario recibe un
                correo con un enlace para establecer la suya propia.
              </p>
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
        ) : usuarios.length === 0 ? (
          <p className="text-sm text-zinc-500">No hay usuarios que mostrar.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-zinc-200 bg-white shadow-sm">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-zinc-200 text-xs uppercase tracking-wide text-zinc-500">
                  <th className="px-5 py-3 font-medium">Email</th>
                  <th className="px-5 py-3 font-medium">Rol</th>
                  <th className="px-5 py-3 font-medium">Estado</th>
                  <th className="px-5 py-3 font-medium text-right">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {usuarios.map((u) =>
                  editandoId === u.id ? (
                    <tr key={u.id} className="border-b border-zinc-100 last:border-0">
                      <td colSpan={4} className="p-0">
                        <form onSubmit={handleEditar} className="flex flex-wrap items-end gap-3 p-5">
                          <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
                            Email
                            <input
                              type="email"
                              value={editEmail}
                              onChange={(e) => setEditEmail(e.target.value)}
                              required
                              className="rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
                            />
                          </label>
                          <label className="flex flex-col gap-1 text-sm font-medium text-zinc-700">
                            Rol
                            <select
                              value={editRol}
                              onChange={(e) => setEditRol(e.target.value as Rol)}
                              className="rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
                            >
                              {ROLES.map((r) => (
                                <option key={r} value={r}>
                                  {r}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="flex items-center gap-2 pb-2 text-sm font-medium text-zinc-700">
                            <input
                              type="checkbox"
                              checked={editActivo}
                              onChange={(e) => setEditActivo(e.target.checked)}
                              className="h-4 w-4 rounded border-zinc-300"
                            />
                            Activo
                          </label>
                          <div className="ml-auto flex gap-2 pb-1">
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
                        </form>
                      </td>
                    </tr>
                  ) : (
                    <tr key={u.id} className="border-b border-zinc-100 last:border-0">
                      <td className="px-5 py-3">
                        <div className="font-medium text-zinc-900">{u.email}</div>
                        <div className="text-xs text-zinc-500">{u.nombre}</div>
                      </td>
                      <td className="px-5 py-3 capitalize text-zinc-700">{u.rol}</td>
                      <td className="px-5 py-3">
                        <span
                          className={
                            u.activo
                              ? "rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700"
                              : "rounded-full bg-zinc-100 px-2 py-1 text-xs font-medium text-zinc-500"
                          }
                        >
                          {u.activo ? "Activo" : "Inactivo"}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-right">
                        <div className="flex justify-end gap-2">
                          <button
                            onClick={() => startEdit(u)}
                            className="rounded-lg px-3 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
                          >
                            Editar
                          </button>
                          <button
                            onClick={() => void handleEliminar(u.id, u.email)}
                            disabled={u.id === user?.id}
                            title={u.id === user?.id ? "No puedes eliminar tu propio usuario" : undefined}
                            className="rounded-lg px-3 py-1.5 text-sm text-red-500 transition-colors hover:bg-red-50 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent"
                          >
                            Eliminar
                          </button>
                        </div>
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
