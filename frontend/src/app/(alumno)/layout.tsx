"use client";

/** Protege las rutas del alumno y redirige los roles no autorizados. */

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../lib/auth-context";

export default function AlumnoLayout({ children }: { children: ReactNode }) {
  const { token, user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!token) {
      router.replace("/login");
    } else if (user && user.rol === "profesor") {
      router.replace("/profesor/asignaturas");
    } else if (user && user.rol === "admin") {
      router.replace("/admin/usuarios");
    }
  }, [token, user, router]);

  if (!token || (user && user.rol !== "alumno")) {
    return null;
  }

  return <>{children}</>;
}
