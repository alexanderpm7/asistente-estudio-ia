"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../lib/auth-context";

export default function AdminLayout({ children }: { children: ReactNode }) {
  const { token, user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!token) {
      router.replace("/login");
    } else if (user && user.rol === "profesor") {
      router.replace("/profesor/asignaturas");
    } else if (user && user.rol === "alumno") {
      router.replace("/alumno/asignaturas");
    }
  }, [token, user, router]);

  if (!token || (user && user.rol !== "admin")) {
    return null;
  }

  return <>{children}</>;
}
