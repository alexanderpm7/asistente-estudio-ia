"use client";

/** Chat por asignatura con historial paginado y respuestas SSE. */

import {
  useRef,
  useState,
  useEffect,
  useLayoutEffect,
  type KeyboardEvent,
} from "react";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "../../../lib/auth-context";
import { authHeader, extraerDetalleError } from "../../../lib/api";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface HistorialMensaje {
  id: string;
  rol: "user" | "assistant";
  contenido: string;
}

interface HistorialResponse {
  mensajes: HistorialMensaje[];
  has_more: boolean;
}

interface AsignaturaResponse {
  id: string;
  nombre: string;
}

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

function parseSSEChunk(
  buffer: string,
): { tokens: string[]; remaining: string } {
  const parts = buffer.split(/\r?\n\r?\n/);
  const remaining = parts.pop() ?? "";
  const tokens: string[] = [];
  for (const part of parts) {
    for (const line of part.split(/\r?\n/)) {
      if (line.startsWith("data: ")) {
        const value = line.slice(6);
        if (value) tokens.push(value);
      }
    }
  }
  return { tokens, remaining };
}

function toMessage(m: HistorialMensaje): Message {
  return { id: m.id, role: m.rol, content: m.contenido };
}

export default function ChatPage() {
  const params = useParams<{ asignaturaId: string }>();
  const asignaturaId = params.asignaturaId;
  const { token, setToken, setUser } = useAuth();
  const router = useRouter();

  const [messages, setMessages] = useState<Message[]>([]);
  const [nombreAsignatura, setNombreAsignatura] = useState<string | null>(null);
  const [historialCargado, setHistorialCargado] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mainRef = useRef<HTMLElement>(null);
  const topSentinelRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const messagesRef = useRef<Message[]>(messages);
  const skipAutoScrollRef = useRef(false);
  const scrollAdjustRef = useRef<{ prevScrollHeight: number; prevScrollTop: number } | null>(
    null,
  );

  // La cabecera usa el UUID como fallback si no puede cargar el nombre.
  useEffect(() => {
    if (!token) return;

    void (async () => {
      try {
        const res = await fetch(`/api/asignaturas/${asignaturaId}`, {
          headers: authHeader(token),
        });
        if (!res.ok) return;
        const data = (await res.json()) as AsignaturaResponse;
        setNombreAsignatura(data.nombre);
      } catch {
      }
    })();
  }, [token, asignaturaId]);

  useEffect(() => {
    if (!token || historialCargado) return;

    void (async () => {
      try {
        const res = await fetch(`/api/chat/${asignaturaId}/historial`, {
          headers: authHeader(token),
        });
        if (!res.ok) return;
        const data = (await res.json()) as HistorialResponse;
        setMessages(data.mensajes.map(toMessage));
        setHasMore(data.has_more);
      } catch {
      } finally {
        setHistorialCargado(true);
      }
    })();
  }, [token, asignaturaId, historialCargado]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Anteponer mensajes sin cambiar la posición visual del scroll.
  async function cargarMensajesAnteriores() {
    const oldest = messagesRef.current[0];
    if (!token || loadingOlder || !hasMore || !oldest) return;

    setLoadingOlder(true);
    const container = mainRef.current;
    if (container) {
      scrollAdjustRef.current = {
        prevScrollHeight: container.scrollHeight,
        prevScrollTop: container.scrollTop,
      };
    }

    try {
      const res = await fetch(
        `/api/chat/${asignaturaId}/historial?before=${oldest.id}`,
        { headers: authHeader(token) },
      );
      if (!res.ok) return;
      const data = (await res.json()) as HistorialResponse;
      skipAutoScrollRef.current = true;
      setMessages((prev) => [...data.mensajes.map(toMessage), ...prev]);
      setHasMore(data.has_more);
    } catch {
    } finally {
      setLoadingOlder(false);
    }
  }

  useEffect(() => {
    const sentinel = topSentinelRef.current;
    const root = mainRef.current;
    if (!sentinel || !root || !hasMore || loadingOlder) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          void cargarMensajesAnteriores();
        }
      },
      { root, threshold: 0 },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasMore, loadingOlder, token, asignaturaId]);

  // Debe ser síncrono para ocultar el centinela antes de que el observer cargue otra página.
  useLayoutEffect(() => {
    if (historialCargado) {
      bottomRef.current?.scrollIntoView({ behavior: "instant" });
    }
  }, [historialCargado]);

  // Restaura la posición antes del siguiente frame para evitar saltos.
  useLayoutEffect(() => {
    const adjust = scrollAdjustRef.current;
    const container = mainRef.current;
    if (adjust && container) {
      container.scrollTop =
        container.scrollHeight - adjust.prevScrollHeight + adjust.prevScrollTop;
      scrollAdjustRef.current = null;
    }
  }, [messages]);

  useEffect(() => {
    if (skipAutoScrollRef.current) {
      skipAutoScrollRef.current = false;
      return;
    }
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend(e?: { preventDefault(): void }) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || isStreaming || !token) return;

    const userMsgId = crypto.randomUUID();
    const assistantMsgId = crypto.randomUUID();

    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: "user", content: text },
      { id: assistantMsgId, role: "assistant", content: "" },
    ]);
    setInput("");
    setIsStreaming(true);
    setError(null);

    try {
      // El proxy de Next.js bufferiza SSE; el backend permite recibir tokens progresivamente.
      const res = await fetch(`${BACKEND_URL}/chat/${asignaturaId}`, {
        method: "POST",
        headers: { ...authHeader(token), "Content-Type": "application/json" },
        body: JSON.stringify({ mensaje: text }),
      });

      if (!res.ok) {
        setMessages((prev) => prev.filter((m) => m.id !== assistantMsgId));
        setError(await extraerDetalleError(res));
        return;
      }

      if (!res.body) {
        setError("El servidor no devolvió un stream.");
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const { tokens, remaining } = parseSSEChunk(buffer);
        buffer = remaining;

        if (tokens.length > 0) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId
                ? { ...m, content: m.content + tokens.join("") }
                : m,
            ),
          );
        }
      }

      const { tokens: finalTokens } = parseSSEChunk(buffer + "\n\n");
      if (finalTokens.length > 0) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content: m.content + finalTokens.join("") }
              : m,
          ),
        );
      }
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Error de conexión con el servidor.";
      setError(message);
      setMessages((prev) => prev.filter((m) => m.id !== assistantMsgId));
    } finally {
      setIsStreaming(false);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  return (
    <div className="flex h-screen flex-col bg-zinc-50">
      <header className="flex items-center justify-between border-b border-zinc-200 bg-white px-6 py-3 shadow-sm">
        <div>
          <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">
            Asignatura
          </p>
          <h1 className="text-base font-semibold text-zinc-900">
            {nombreAsignatura ?? asignaturaId}
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/alumno/asignaturas")}
            className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
          >
            <svg
              className="h-4 w-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <polyline points="15 18 9 12 15 6" />
            </svg>
            Mis asignaturas
          </button>
          <button
            onClick={() => {
              setToken(null);
              setUser(null);
            }}
            className="rounded-lg px-3 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
          >
            Cerrar sesión
          </button>
        </div>
      </header>

      {/* ── Lista de mensajes ── */}
      <main ref={mainRef} className="flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {hasMore && (
            <div ref={topSentinelRef} className="flex justify-center py-2">
              {loadingOlder && (
                <p className="text-xs text-zinc-500">Cargando mensajes anteriores…</p>
              )}
            </div>
          )}

          {messages.length === 0 && historialCargado && (
            <p className="text-center text-sm text-zinc-500">
              Escribe tu primera pregunta sobre la asignatura.
            </p>
          )}

          {!historialCargado && (
            <p className="text-center text-sm text-zinc-500">
              Cargando historial…
            </p>
          )}

          {messages.map((msg) => {
            const isUser = msg.role === "user";
            const isLastAssistant =
              !isUser && msg.id === messages[messages.length - 1]?.id;
            const showCursor =
              isLastAssistant && isStreaming && msg.content === "";

            return (
              <div
                key={msg.id}
                className={`flex ${isUser ? "justify-end" : "justify-start"}`}
              >
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
                    {isUser ? "Tú" : "Asistente"}
                  </p>
                  <p className="whitespace-pre-wrap">
                    {msg.content}
                    {showCursor && (
                      <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-zinc-400 align-middle" />
                    )}
                  </p>
                </div>
              </div>
            );
          })}

          <div ref={bottomRef} />
        </div>
      </main>

      {/* ── Banner de error ── */}
      {error && (
        <div className="mx-auto mb-2 w-full max-w-2xl px-4">
          <div className="flex items-start justify-between rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
            <span>{error}</span>
            <button
              onClick={() => setError(null)}
              className="ml-4 font-semibold leading-none text-red-500 transition-colors hover:text-red-700"
              aria-label="Cerrar error"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* ── Formulario de entrada ── */}
      <form
        onSubmit={handleSend}
        className="border-t border-zinc-200 bg-white px-4 py-4"
      >
        <div className="mx-auto flex max-w-2xl items-end gap-3">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming}
            rows={1}
            placeholder={
              isStreaming ? "Esperando respuesta…" : "Escribe tu pregunta…"
            }
            className="flex-1 resize-none rounded-xl border border-zinc-300 px-4 py-2.5 text-sm text-zinc-900 placeholder-zinc-400 outline-none focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200 disabled:bg-zinc-50 disabled:text-zinc-400"
            style={{ maxHeight: "8rem" }}
          />
          <button
            type="submit"
            disabled={isStreaming || !input.trim()}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-zinc-900 text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Enviar mensaje"
          >
            {isStreaming ? (
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z"
                />
              </svg>
            ) : (
              <svg
                className="h-4 w-4 translate-x-0.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            )}
          </button>
        </div>
        <p className="mx-auto mt-1.5 max-w-2xl text-center text-xs text-zinc-500">
          Enter para enviar · Shift+Enter para nueva línea
        </p>
      </form>
    </div>
  );
}
