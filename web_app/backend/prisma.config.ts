import path from "node:path";
import { defineConfig } from "prisma/config";

// Prisma 7 no longer accepts the connection URL inside schema.prisma. It lives
// here for migrations, and is passed to PrismaClient via an adapter at runtime.
//
// DATABASE_URL carries "?schema=public", which Prisma understands. Note that
// libpq tools (psql, pg_dump) do NOT — scripts/backup.sh strips it.
export default defineConfig({
  schema: path.join("prisma", "schema.prisma"),
  migrations: {
    path: path.join("prisma", "migrations"),
  },
  datasource: {
    url: process.env.DATABASE_URL,
  },
});
