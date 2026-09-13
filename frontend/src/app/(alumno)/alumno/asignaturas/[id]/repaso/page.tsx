"use client";

/** Genera flashcards y preguntas de práctica para una asignatura. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "../../../../../lib/auth-context";
import { NavBar } from "../../../../../../components/NavBar";
import { authHeader, extraerDetalleError } from "../../../../../lib/api";

interface Documento {
  id: string;
  nombre: string;
  n_chunks: number;
}

interface Flashcard {
  anverso: string;
  reverso: string;
}

interface Pregunta {
  enunciado: string;
  opciones: string[];
  respuesta_correcta: number;
}

interface RepasoData {
  asignatura_id: string;
  flashcards: Flashcard[];
  preguntas: Pregunta[];
}

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

function FlashcardItem({
  card,
  index,
}: {
  card: Flashcard;
  index: number;
}) {
  const [flipped, setFlipped] = useState(false);

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Flashcard ${index + 1}: ${flipped ? "reverso" : "anverso"}`}
      onClick={() => setFlipped((v) => !v)}
      onKeyDown={(e) => e.key === "Enter" && setFlipped((v) => !v)}
      className={[
        "cursor-pointer select-none rounded-xl border p-6 transition-colors duration-200",
        flipped
          ? "border-zinc-300 bg-zinc-800 text-white"
          : "border-zinc-200 bg-white text-zinc-900",
      ].join(" ")}
    >
      <p
        className={[
          "mb-1 text-xs font-semibold uppercase tracking-widest",
          flipped ? "text-zinc-400" : "text-zinc-500",
        ].join(" ")}
      >
        {flipped ? "Reverso" : "Anverso"} · haz clic para voltear
      </p>
      <p className="text-base leading-relaxed">
        {flipped ? card.reverso : card.anverso}
      </p>
    </div>
  );
}

function PreguntaItem({
  pregunta,
  index,
  onAnswer,
}: {
  pregunta: Pregunta;
  index: number;
  onAnswer: (correct: boolean) => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);

  function handleSelect(i: number) {
    if (selected !== null) return;
    setSelected(i);
    onAnswer(i === pregunta.respuesta_correcta);
  }

  function optionStyle(i: number): string {
    const base =
      "w-full rounded-lg border px-4 py-2.5 text-left text-sm transition-colors";
    if (selected === null) {
      return `${base} border-zinc-200 bg-white hover:bg-zinc-50 text-zinc-800`;
    }
    if (i === pregunta.respuesta_correcta) {
      return `${base} border-emerald-400 bg-emerald-50 text-emerald-800 font-medium`;
    }
    if (i === selected) {
      return `${base} border-red-400 bg-red-50 text-red-800 font-medium`;
    }
    return `${base} border-zinc-100 bg-zinc-50 text-zinc-500`;
  }

  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
      <p className="mb-1 text-xs font-semibold uppercase tracking-widest text-zinc-500">
        Pregunta {index + 1}
      </p>
      <p className="mb-4 text-sm font-medium text-zinc-900">{pregunta.enunciado}</p>
      <div className="flex flex-col gap-2">
        {pregunta.opciones.map((op, i) => (
          <button key={i} className={optionStyle(i)} onClick={() => handleSelect(i)}>
            <span className="mr-2 font-mono font-bold">
              {["A", "B", "C", "D"][i]}.
            </span>
            {op}
            {selected !== null && i === pregunta.respuesta_correcta && (
              <span className="ml-2">✓</span>
            )}
            {selected !== null && i === selected && i !== pregunta.respuesta_correcta && (
              <span className="ml-2">✗</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function RepasoPage() {
  const params = useParams<{ id: string }>();
  const asignaturaId = params.id;
  const { token } = useAuth();

  const [repaso, setRepaso] = useState<RepasoData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Se cargan antes de permitir generar el repaso.
  const [documentos, setDocumentos] = useState<Documento[] | null>(null);
  const [documentosError, setDocumentosError] = useState<string | null>(null);
  const [documentoId, setDocumentoId] = useState("");

  const [aciertos, setAciertos] = useState(0);
  const [respondidas, setRespondidas] = useState(0);

  useEffect(() => {
    if (!token) return;
    let cancelado = false;

    (async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/asignaturas/${asignaturaId}/documentos`, {
          headers: authHeader(token),
        });
        if (!res.ok) throw new Error(await extraerDetalleError(res));
        const data = (await res.json()) as Documento[];
        if (!cancelado) setDocumentos(data);
      } catch (err) {
        if (!cancelado) {
          setDocumentosError(
            err instanceof Error ? err.message : "No se pudieron cargar los documentos.",
          );
        }
      }
    })();

    return () => {
      cancelado = true;
    };
  }, [token, asignaturaId]);

  function handleAnswer(correct: boolean) {
    setRespondidas((n) => n + 1);
    if (correct) setAciertos((n) => n + 1);
  }

  async function generarRepaso() {
    if (!token || !documentoId) return;
    setLoading(true);
    setError(null);
    setRepaso(null);
    setAciertos(0);
    setRespondidas(0);

    try {
      const query = new URLSearchParams({ documento_id: documentoId });
      const res = await fetch(
        `${BACKEND_URL}/asignaturas/${asignaturaId}/repaso?${query.toString()}`,
        { method: "POST", headers: authHeader(token) },
      );
      if (!res.ok) throw new Error(await extraerDetalleError(res));
      setRepaso((await res.json()) as RepasoData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al generar el repaso.");
    } finally {
      setLoading(false);
    }
  }

  const totalPreguntas = repaso?.preguntas.length ?? 0;
  // Un documento recién subido puede seguir indexándose en segundo plano.
  const documentosIndexados = documentos?.filter((d) => d.n_chunks > 0) ?? [];

  return (
    <div className="min-h-screen bg-zinc-50">
      <NavBar
        title="Modo repaso"
        backHref={`/alumno/asignaturas`}
        backLabel="Mis asignaturas"
      />

      <main className="mx-auto max-w-3xl space-y-8 px-4 py-8">

        {!loading && (
          <div className="space-y-3">
            <p className="text-sm text-zinc-500">
              Elige el documento del que quieres generar flashcards y preguntas de práctica.
            </p>

            {documentos === null && !documentosError && (
              <p className="text-sm text-zinc-500">Cargando documentos…</p>
            )}

            {documentosError && (
              <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
                {documentosError}
              </div>
            )}

            {documentos !== null && !documentosError && documentosIndexados.length === 0 && (
              <div className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
                Esta asignatura todavía no tiene documentos indexados. Pide a tu profesor
                que suba apuntes antes de generar un repaso.
              </div>
            )}

            {documentosIndexados.length > 0 && (
              <div className="flex items-center gap-3">
                <select
                  value={documentoId}
                  onChange={(e) => setDocumentoId(e.target.value)}
                  className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
                >
                  <option value="">Selecciona un documento…</option>
                  {documentosIndexados.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.nombre}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => void generarRepaso()}
                  className="shrink-0 rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={loading || !documentoId}
                >
                  {repaso ? "Regenerar repaso" : "Generar repaso"}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Spinner */}
        {loading && (
          <div className="flex flex-col items-center gap-3 py-16">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-zinc-300 border-t-zinc-900" />
            <p className="text-sm text-zinc-500">Generando repaso… puede tardar unos segundos.</p>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        )}

        {/* Flashcards */}
        {repaso && repaso.flashcards.length > 0 && (
          <section>
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-zinc-500">
              Flashcards ({repaso.flashcards.length})
            </h2>
            <div className="flex flex-col gap-3">
              {repaso.flashcards.map((card, i) => (
                <FlashcardItem key={i} card={card} index={i} />
              ))}
            </div>
          </section>
        )}

        {/* Preguntas de práctica */}
        {repaso && repaso.preguntas.length > 0 && (
          <section>
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-zinc-500">
              Preguntas de práctica ({repaso.preguntas.length})
            </h2>
            <div className="flex flex-col gap-4">
              {repaso.preguntas.map((pq, i) => (
                <PreguntaItem
                  key={i}
                  pregunta={pq}
                  index={i}
                  onAnswer={handleAnswer}
                />
              ))}
            </div>

            {/* Contador de aciertos (aparece al responder todas) */}
            {respondidas === totalPreguntas && totalPreguntas > 0 && (
              <div
                className={[
                  "mt-6 rounded-xl border px-6 py-4 text-center",
                  aciertos === totalPreguntas
                    ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                    : "border-zinc-200 bg-white text-zinc-800",
                ].join(" ")}
              >
                <p className="text-lg font-bold">
                  {aciertos} / {totalPreguntas} correctas
                </p>
                <p className="mt-1 text-sm">
                  {aciertos === totalPreguntas
                    ? "¡Perfecto! Has respondido todo correctamente."
                    : aciertos >= Math.ceil(totalPreguntas / 2)
                    ? "Buen resultado. Repasa los conceptos en los que fallaste."
                    : "Sigue practicando. Vuelve a los apuntes y regenera el repaso."}
                </p>
              </div>
            )}
          </section>
        )}

        {/* Enlace al chat */}
        {repaso && (
          <div className="flex justify-end">
            <Link
              href={`/chat/${asignaturaId}`}
              className="rounded text-sm text-zinc-500 underline transition-colors hover:text-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
            >
              ¿Tienes dudas? Ve al chat →
            </Link>
          </div>
        )}
      </main>
    </div>
  );
}
