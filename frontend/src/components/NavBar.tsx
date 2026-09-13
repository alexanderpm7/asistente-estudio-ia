"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "../app/lib/auth-context";

interface NavBarProps {
  title?: string;
  backHref?: string;
  backLabel?: string;
}

/** Flecha "volver" — mismo estilo de trazo que el resto de iconos inline de la app. */
function ChevronLeftIcon() {
  return (
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
  );
}

export function NavBar({ title, backHref, backLabel }: NavBarProps) {
  const router = useRouter();
  const { user, setToken, setUser } = useAuth();

  function handleLogout() {
    setToken(null);
    setUser(null);
    router.push("/login");
  }

  return (
    <header className="sticky top-0 z-50 border-b border-zinc-200 bg-white px-6 py-3 shadow-sm">
      <div className="mx-auto flex max-w-5xl items-center justify-between">
        <div className="flex items-center gap-3">
          {backHref && (
            <Link
              href={backHref}
              className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
            >
              <ChevronLeftIcon />
              {backLabel ?? "Volver"}
            </Link>
          )}
          {title && (
            <h1 className="text-base font-semibold text-zinc-900">{title}</h1>
          )}
        </div>
        <div className="flex items-center gap-4">
          {user && (
            <span className="text-xs text-zinc-500">
              {user.nombre}
            </span>
          )}
          <button
            onClick={handleLogout}
            className="rounded-lg px-3 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-300 focus-visible:ring-offset-2"
          >
            Cerrar sesión
          </button>
        </div>
      </div>
    </header>
  );
}
