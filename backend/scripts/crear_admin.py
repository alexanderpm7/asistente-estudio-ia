"""Bootstrap del primer usuario admin — RF-A1..RF-A4.

`POST /auth/register` solo admite el rol "alumno" (registro público cerrado
para roles privilegiados, ver `app/schemas/auth.py`). Este script es la única
vía para crear el primer administrador del sistema; el resto de admins y
profesores se dan de alta desde el panel de administración una vez existe
al menos uno.

Idempotente: si ya existe un usuario con ese email, no lo duplica y avisa.

Uso:
    # Por argumentos
    python -m scripts.crear_admin --email admin@uni.es --password "Segura1234!" [--nombre "Admin"]

    # Por variables de entorno (útil en despliegue automatizado)
    ADMIN_EMAIL=admin@uni.es ADMIN_PASSWORD='Segura1234!' python -m scripts.crear_admin

Se ejecuta desde `backend/` con el entorno virtual activado (usa la misma
`DATABASE_URL` que la aplicación, vía `app.core.config.settings`).
"""

import argparse
import asyncio
import os
import sys
import uuid

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import RolUsuario, Usuario
from app.db.session import AsyncSessionLocal


async def crear_admin(email: str, password: str, nombre: str) -> None:
    async with AsyncSessionLocal() as session:
        existente = await session.execute(select(Usuario).where(Usuario.email == email))
        if existente.scalar_one_or_none() is not None:
            print(f"Ya existe un usuario con el email {email!r}; no se duplica.")
            return

        admin = Usuario(
            id=uuid.uuid4(),
            email=email,
            password_hash=hash_password(password),
            nombre=nombre,
            rol=RolUsuario.admin,
        )
        session.add(admin)
        await session.commit()
        print(f"Admin creado: {email}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crea el primer usuario admin del sistema.")
    parser.add_argument("--email", default=os.environ.get("ADMIN_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
    parser.add_argument("--nombre", default=os.environ.get("ADMIN_NOMBRE", "Administrador"))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.email or not args.password:
        print(
            "Faltan --email/--password (o ADMIN_EMAIL/ADMIN_PASSWORD en el entorno).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    asyncio.run(crear_admin(args.email, args.password, args.nombre))


if __name__ == "__main__":
    main()
