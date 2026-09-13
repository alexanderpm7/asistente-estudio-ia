"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useAuth } from "../../../lib/auth-context";
import { NavBar } from "../../../../components/NavBar";
import { authHeader, extraerDetalleError } from "../../../lib/api";

interface Asignatura {
  id: string;
  nombre: string;
  descripcion: string | null;
}

interface Alumno {
  alumno_id: string;
  email: string;
  nombre: string;
  matriculado_en: string;
}

interface Documento {
  id: string;
  nombre: string;
  asignatura_id: string;
  subido_en: string;
  n_chunks: number;
}

// Debe coincidir con el límite equivalente del backend.
const PROMPT_MAX_LENGTH = 2000;

/** Sube el fichero con XHR para obtener progreso real de transferencia. */
function uploadWithProgress(
  url: string,
  token: string,
  file: File,
  onProgress: (pct: number) => void,
): Promise<Documento> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("file", file);

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as Documento);
      } else {
        try {
          const err = JSON.parse(xhr.responseText) as { detail?: string };
          reject(new Error(err.detail ?? `Error ${xhr.status}`));
        } catch {
          reject(new Error(`Error ${xhr.status}`));
        }
      }
    });

    xhr.addEventListener("error", () => reject(new Error("Error de red al subir el fichero.")));
    xhr.addEventListener("abort", () => reject(new Error("Subida cancelada.")));

    xhr.open("POST", url);
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.send(formData);
  });
}

/** Icono de spinner SVG inline, reutilizable. */
function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z"
      />
    </svg>
  );
}

const POLL_INTERVAL_MS = 4_000;
const POLL_MAX_MS = 5 * 60_000; // 5 minutos
// Cuenta intentos para mantener una duración máxima determinista.
const POLL_MAX_ATTEMPTS = Math.ceil(POLL_MAX_MS / POLL_INTERVAL_MS);

export default function AsignaturaDetailPage() {
  const params = useParams<{ id: string }>();
  const asignaturaId = params.id;
  const { token } = useAuth();

  const [asignatura, setAsignatura] = useState<Asignatura | null>(null);
  const [alumnos, setAlumnos] = useState<Alumno[]>([]);
  const [documentos, setDocumentos] = useState<Documento[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [emailAlumno, setEmailAlumno] = useState("");
  const [alumnoLoading, setAlumnoLoading] = useState(false);
  const [alumnoError, setAlumnoError] = useState<string | null>(null);

  const [sugerenciasEmail, setSugerenciasEmail] = useState<string[]>([]);
  const [mostrarSugerencias, setMostrarSugerencias] = useState(false);
  const sugerenciasTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [deletingDocId, setDeletingDocId] = useState<string | null>(null);

  const [promptRedactor, setPromptRedactor] = useState("");
  const [promptRedactorLoading, setPromptRedactorLoading] = useState(true);
  const [promptRedactorSaving, setPromptRedactorSaving] = useState(false);
  const [promptRedactorError, setPromptRedactorError] = useState<string | null>(null);
  const [promptRedactorGuardado, setPromptRedactorGuardado] = useState(false);

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollAttemptsRef = useRef(0);

  const fetchDocumentos = useCallback(async (): Promise<Documento[]> => {
    const res = await fetch(`/api/asignaturas/${asignaturaId}/documentos`, {
      headers: authHeader(token),
    });
    if (!res.ok) return [];
    return (await res.json()) as Documento[];
     
  }, [asignaturaId, token]);

  /** Inicia el polling hasta que todos los docs tengan n_chunks > 0 o expire el timeout. */
  function startPolling() {
    pollAttemptsRef.current = 0;
    schedulePoll();
  }

  function schedulePoll() {
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    pollTimerRef.current = setTimeout(async () => {
      pollAttemptsRef.current += 1;
      const docs = await fetchDocumentos();
      setDocumentos(docs);
      const hayPendientes = docs.some((d) => d.n_chunks === 0);
      const tiempoAgotado = pollAttemptsRef.current >= POLL_MAX_ATTEMPTS;
      if (hayPendientes && !tiempoAgotado) {
        schedulePoll();
      }
    }, POLL_INTERVAL_MS);
  }

  // Limpiar el timer al desmontar
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, []);

  const fetchAll = useCallback((): Promise<void> => {
    if (!token) return Promise.resolve();
    return Promise.all([
      fetch(`/api/asignaturas/${asignaturaId}`, { headers: authHeader(token) }),
      fetch(`/api/asignaturas/${asignaturaId}/alumnos`, { headers: authHeader(token) }),
      fetchDocumentos(),
      fetch(`/api/asignaturas/${asignaturaId}/prompt-redactor`, { headers: authHeader(token) }),
    ])
      .then(async ([asigRes, alumRes, docs, promptRes]) => {
        if (!asigRes.ok) throw new Error(`Error cargando asignatura: ${asigRes.status}`);
        setAsignatura((await asigRes.json()) as Asignatura);
        setAlumnos(alumRes.ok ? ((await alumRes.json()) as Alumno[]) : []);
        setDocumentos(docs);
        if (promptRes.ok) {
          const body = (await promptRes.json()) as { prompt_redactor: string | null };
          setPromptRedactor(body.prompt_redactor ?? "");
        }
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Error al cargar los datos.");
      })
      .finally(() => {
        setLoading(false);
        setPromptRedactorLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, asignaturaId]);

  async function handleGuardarPromptRedactor() {
    setPromptRedactorSaving(true);
    setPromptRedactorError(null);
    setPromptRedactorGuardado(false);
    try {
      const res = await fetch(`/api/asignaturas/${asignaturaId}`, {
        method: "PUT",
        headers: { ...authHeader(token), "Content-Type": "application/json" },
        body: JSON.stringify({ prompt_redactor: promptRedactor }),
      });
      if (!res.ok) {
        throw new Error(await extraerDetalleError(res));
      }
      setPromptRedactorGuardado(true);
    } catch (err) {
      setPromptRedactorError(
        err instanceof Error ? err.message : "Error al guardar el prompt de estilo.",
      );
    } finally {
      setPromptRedactorSaving(false);
    }
  }

  useEffect(() => {
    void fetchAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, asignaturaId]);

  // Busca alumnos con un mínimo de tres caracteres.
  useEffect(() => {
    if (sugerenciasTimerRef.current) clearTimeout(sugerenciasTimerRef.current);
    const q = emailAlumno.trim();
    if (!token || q.length < 3) return;
    sugerenciasTimerRef.current = setTimeout(() => {
      void (async () => {
        try {
          const res = await fetch(
            `/api/asignaturas/alumnos/buscar?q=${encodeURIComponent(q)}`,
            { headers: authHeader(token) },
          );
          if (res.ok) setSugerenciasEmail((await res.json()) as string[]);
        } catch {
        }
      })();
    }, 250);
    return () => {
      if (sugerenciasTimerRef.current) clearTimeout(sugerenciasTimerRef.current);
    };
     
  }, [emailAlumno, token]);

  function seleccionarSugerencia(email: string) {
    setEmailAlumno(email);
    setSugerenciasEmail([]);
    setMostrarSugerencias(false);
  }

  async function handleMatricular(e: { preventDefault(): void }) {
    e.preventDefault();
    setAlumnoLoading(true);
    setAlumnoError(null);
    try {
      const res = await fetch(`/api/asignaturas/${asignaturaId}/alumnos`, {
        method: "POST",
        headers: { ...authHeader(token), "Content-Type": "application/json" },
        body: JSON.stringify({ email: emailAlumno }),
      });
      if (!res.ok) {
        throw new Error(await extraerDetalleError(res));
      }
      setEmailAlumno("");
      const alumRes = await fetch(`/api/asignaturas/${asignaturaId}/alumnos`, {
        headers: authHeader(token),
      });
      if (alumRes.ok) setAlumnos((await alumRes.json()) as Alumno[]);
    } catch (err) {
      setAlumnoError(err instanceof Error ? err.message : "Error al matricular.");
    } finally {
      setAlumnoLoading(false);
    }
  }

  async function handleDesmatricular(alumnoId: string, nombre: string) {
    if (!confirm(`¿Dar de baja a "${nombre}" de esta asignatura?`)) return;
    try {
      const res = await fetch(`/api/asignaturas/${asignaturaId}/alumnos/${alumnoId}`, {
        method: "DELETE",
        headers: authHeader(token),
      });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      setAlumnos((prev) => prev.filter((a) => a.alumno_id !== alumnoId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al desmatricular.");
    }
  }

  async function handleBorrarDocumento(docId: string, nombre: string) {
    if (!confirm(`¿Eliminar "${nombre}"? Se borrarán todos sus fragmentos del RAG.`)) return;
    setDeletingDocId(docId);
    try {
      const res = await fetch(`/api/asignaturas/${asignaturaId}/documentos/${docId}`, {
        method: "DELETE",
        headers: authHeader(token),
      });
      if (!res.ok) {
        throw new Error(await extraerDetalleError(res));
      }
      setDocumentos((prev) => prev.filter((d) => d.id !== docId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al borrar el documento.");
    } finally {
      setDeletingDocId(null);
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !token) return;

    setUploadProgress(0);
    setUploadError(null);

    try {
      const doc = await uploadWithProgress(
        `/api/asignaturas/${asignaturaId}/documentos`,
        token,
        file,
        setUploadProgress,
      );
      setDocumentos((prev) => [...prev, doc]);
      startPolling();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Error al subir el fichero.");
    } finally {
      setUploadProgress(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  const hayIndexando = documentos.some((d) => d.n_chunks === 0);
  // Deriva la visibilidad del texto para evitar sugerencias obsoletas.
  const sugerenciasVisibles = emailAlumno.trim().length >= 3 ? sugerenciasEmail : [];

  return (
    <div className="min-h-screen bg-zinc-50">
      <NavBar
        title={asignatura?.nombre ?? "Asignatura"}
        backHref="/profesor/asignaturas"
        backLabel="Mis asignaturas"
      />

      <main className="mx-auto max-w-3xl space-y-8 px-4 py-8">
        {loading && <p className="text-sm text-zinc-500">Cargando…</p>}

        {error && (
          <div className="flex items-start justify-between rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
            <span>{error}</span>
            <button
              onClick={() => setError(null)}
              className="ml-4 font-semibold text-red-500 transition-colors hover:text-red-700"
            >
              ✕
            </button>
          </div>
        )}

        {asignatura && (
          <section>
            <h2 className="mb-1 text-xl font-semibold text-zinc-900">{asignatura.nombre}</h2>
          </section>
        )}

        {/* ── Alumnos ── */}
        <section className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
          <h3 className="mb-4 text-sm font-semibold text-zinc-800">
            Alumnos matriculados ({alumnos.length})
          </h3>

          <form onSubmit={handleMatricular} className="mb-4 flex gap-2">
            <div className="relative flex-1">
              <input
                type="email"
                value={emailAlumno}
                onChange={(e) => {
                  setEmailAlumno(e.target.value);
                  setMostrarSugerencias(true);
                }}
                onFocus={() => setMostrarSugerencias(true)}
                onBlur={() => setMostrarSugerencias(false)}
                required
                placeholder="correo@alumno.es"
                autoComplete="off"
                role="combobox"
                aria-expanded={mostrarSugerencias && sugerenciasVisibles.length > 0}
                aria-controls="sugerencias-email-alumno"
                className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
              />
              {mostrarSugerencias && sugerenciasVisibles.length > 0 && (
                <ul
                  id="sugerencias-email-alumno"
                  role="listbox"
                  className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-lg"
                >
                  {sugerenciasVisibles.map((email) => (
                    <li key={email}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={email === emailAlumno}
                        // onMouseDown (no onClick): se dispara antes que el
                        // onBlur del input, así el clic en la sugerencia
                        // registra antes de que el desplegable se cierre.
                        onMouseDown={(e) => {
                          e.preventDefault();
                          seleccionarSugerencia(email);
                        }}
                        className="block w-full px-3 py-2 text-left text-sm text-zinc-700 transition-colors hover:bg-zinc-50"
                      >
                        {email}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <button
              type="submit"
              disabled={alumnoLoading}
              className="shrink-0 rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {alumnoLoading ? "Añadiendo…" : "Añadir"}
            </button>
          </form>

          {alumnoError && (
            <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">
              {alumnoError}
            </p>
          )}

          {alumnos.length === 0 ? (
            <p className="text-sm text-zinc-500">No hay alumnos matriculados.</p>
          ) : (
            <ul className="divide-y divide-zinc-100">
              {alumnos.map((a) => (
                <li key={a.alumno_id} className="flex items-center justify-between py-2.5">
                  <div>
                    <p className="text-sm font-medium text-zinc-900">{a.nombre}</p>
                    <p className="text-xs text-zinc-500">{a.email}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Link
                      href={`/profesor/asignaturas/${asignaturaId}/historial/${a.alumno_id}`}
                      className="rounded-lg px-3 py-1.5 text-xs text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
                    >
                      Ver historial
                    </Link>
                    <button
                      onClick={() => void handleDesmatricular(a.alumno_id, a.nombre)}
                      className="rounded-lg px-3 py-1.5 text-xs text-red-500 transition-colors hover:bg-red-50 hover:text-red-700"
                    >
                      Dar de baja
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ── Documentos ── */}
        <section className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-zinc-800">
              Documentos / apuntes ({documentos.length})
            </h3>
            {/* Aviso global de indexación en curso */}
            {hayIndexando && (
              <div className="flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700">
                <Spinner className="h-3.5 w-3.5 text-amber-600" />
                Indexando documento, esto puede tardar unos minutos…
              </div>
            )}
          </div>

          {/* Zona de subida */}
          <div className="mb-4">
            <label
              className={[
                "flex cursor-pointer items-center gap-2 rounded-lg border border-dashed px-4 py-3 text-sm transition-colors",
                uploadProgress !== null
                  ? "cursor-not-allowed border-zinc-200 text-zinc-300"
                  : "border-zinc-300 text-zinc-500 hover:border-zinc-400 hover:text-zinc-700",
              ].join(" ")}
            >
              <svg
                className="h-4 w-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
              Subir PDF (máx. 20 MB)
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf"
                className="sr-only"
                onChange={handleFileChange}
                disabled={uploadProgress !== null}
              />
            </label>

            {/* Barra de progreso de transferencia */}
            {uploadProgress !== null && (
              <div className="mt-3">
                <div className="mb-1 flex justify-between text-xs text-zinc-500">
                  <span>Enviando fichero…</span>
                  <span>{uploadProgress}%</span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-100">
                  <div
                    className="h-2 rounded-full bg-zinc-800 transition-all duration-200"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
              </div>
            )}

            {uploadError && (
              <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">
                {uploadError}
              </p>
            )}
          </div>

          {/* Lista de documentos */}
          {documentos.length === 0 ? (
            <p className="text-sm text-zinc-500">No hay documentos subidos.</p>
          ) : (
            <ul className="divide-y divide-zinc-100">
              {documentos.map((d) => (
                <li key={d.id} className="flex items-center justify-between py-2.5">
                  <div>
                    <p className="text-sm font-medium text-zinc-900">{d.nombre}</p>
                    <p className="text-xs text-zinc-500">
                      {new Date(d.subido_en).toLocaleDateString("es-ES")}
                    </p>
                  </div>

                  <div className="flex items-center gap-2">
                    {d.n_chunks === 0 ? (
                      /* Estado: indexando en background */
                      <div className="flex items-center gap-1.5 rounded-md bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
                        <Spinner className="h-3 w-3 text-amber-600" />
                        Indexando…
                      </div>
                    ) : (
                      /* Estado: listo */
                      <span className="rounded-md bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">
                        {d.n_chunks} fragmentos ✓
                      </span>
                    )}
                    <button
                      onClick={() => void handleBorrarDocumento(d.id, d.nombre)}
                      disabled={deletingDocId === d.id}
                      className="flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs text-red-500 transition-colors hover:bg-red-50 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {deletingDocId === d.id ? (
                        <Spinner className="h-3 w-3" />
                      ) : (
                        "Borrar"
                      )}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ── Prompt de estilo del redactor (RF-P8) ── */}
        <section className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
          <h3 className="mb-1 text-sm font-semibold text-zinc-800">
            Estilo de las respuestas del asistente
          </h3>
          <p className="mb-4 text-xs text-zinc-500">
            Este texto controla el <strong>estilo</strong> de las respuestas del asistente
            para esta asignatura (nivel de detalle, tono, formato). Las reglas de seguridad
            y de fidelidad a los apuntes (no inventar información, citar siempre las fuentes
            cuando corresponda) se mantienen siempre activas, al margen de lo que escribas
            aquí.
          </p>

          {promptRedactorLoading ? (
            <p className="text-sm text-zinc-500">Cargando…</p>
          ) : (
            <>
              <textarea
                value={promptRedactor}
                onChange={(e) => {
                  setPromptRedactor(e.target.value);
                  setPromptRedactorGuardado(false);
                }}
                maxLength={PROMPT_MAX_LENGTH}
                rows={8}
                className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
              />
              <div className="mt-1 flex items-center justify-between">
                <span className="text-xs text-zinc-500">
                  {promptRedactor.length} / {PROMPT_MAX_LENGTH} caracteres
                </span>
                {promptRedactorGuardado && (
                  <span className="text-xs font-medium text-emerald-600">Guardado ✓</span>
                )}
              </div>

              {promptRedactorError && (
                <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">
                  {promptRedactorError}
                </p>
              )}

              <button
                onClick={() => void handleGuardarPromptRedactor()}
                disabled={promptRedactorSaving}
                className="mt-3 rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {promptRedactorSaving ? "Guardando…" : "Guardar estilo"}
              </button>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
