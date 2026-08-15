import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient, UserRole } from '@prisma/client';
import * as argon2 from 'argon2';

const prisma = new PrismaClient({
  adapter: new PrismaPg({ connectionString: process.env.DATABASE_URL }),
});

async function main() {
  const email = process.env.ADMIN_EMAIL ?? 'admin@ascras.local';
  const password = process.env.ADMIN_PASSWORD ?? 'ChangeMe_ASCRAS_2026';
  const admin = await prisma.user.upsert({
    where: { email },
    update: {},
    create: {
      email,
      name: 'Administrator',
      passwordHash: await argon2.hash(password),
      role: UserRole.ADMIN,
    },
  });
  console.log(`admin ready: ${admin.email}`);
}

main().finally(() => prisma.$disconnect());
