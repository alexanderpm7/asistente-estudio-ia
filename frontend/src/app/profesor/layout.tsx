"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../lib/auth-context";

export default function ProfesorLayout({ children }: { children: ReactNode }) {
  const { token, user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!token) {
      router.replace("/login");
    } else if (user && user.rol !== "profesor") {
      router.replace("/alumno/asignaturas");
    }
  }, [token, user, router]);

  if (!token || (user && user.rol !== "profesor")) {
    return null;
  }

  return <>{children}</>;
}
