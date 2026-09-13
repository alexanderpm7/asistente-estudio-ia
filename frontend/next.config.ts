import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Imagen mínima para Docker en producción: copia solo los ficheros
  // necesarios a .next/standalone, sin node_modules completo. No afecta a
  // `next dev`.
  output: "standalone",

  // No anunciar el framework en la cabecera "X-Powered-By": Nginx reenvía
  // las cabeceras del frontend tal cual, así que esto hay que desactivarlo
  // en origen.
  poweredByHeader: false,

  // Proxy /api/* → backend FastAPI en desarrollo (localhost:8000).
  // En producción, Nginx enruta /api/* directamente al backend (ver
  // nginx/nginx.prod.conf) con el mismo recorte de prefijo, así que esta
  // rewrite nunca llega a ejecutarse detrás del proxy de producción; se deja
  // para `next dev`/`next start` sin Nginx delante.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/:path*",
      },
    ];
  },
};

export default nextConfig;
