import {
  Injectable,
  Logger,
  UnauthorizedException,
} from '@nestjs/common';
import { UserStatus } from '@prisma/client';
import * as argon2 from 'argon2';
import { createHash, randomBytes } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';

export const SESSION_COOKIE = 'ascras_session';
const SESSION_DAYS = 14;

/**
 * Login-only authentication. There is no self-serve signup by design: an admin
 * creates the account and hands the credentials over personally, so the only
 * public surface here is `login`.
 */
@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);

  constructor(private readonly prisma: PrismaService) {}

  hashPassword(plain: string) {
    return argon2.hash(plain);
  }

  /**
   * Session tokens are stored hashed. A leaked database backup then yields no
   * usable sessions — the same reason passwords are not stored in the clear.
   */
  private hashToken(token: string) {
    return createHash('sha256').update(token).digest('hex');
  }

  async login(email: string, password: string, ip?: string) {
    const user = await this.prisma.user.findUnique({
      where: { email: email.toLowerCase().trim() },
    });

    // Verify against a dummy hash when the user is missing so a wrong address
    // and a wrong password take the same time to answer.
    const hash = user?.passwordHash ?? (await this.dummyHash());
    const ok = await argon2.verify(hash, password).catch(() => false);

    if (!user || !ok) {
      this.logger.warn(`AUDIT login failed for ${email} from ${ip ?? 'unknown'}`);
      throw new UnauthorizedException('Incorrect email or password');
    }
    if (user.status === UserStatus.SUSPENDED) {
      this.logger.warn(`AUDIT suspended user ${user.id} tried to log in`);
      throw new UnauthorizedException('This account is suspended');
    }

    const token = randomBytes(32).toString('base64url');
    const expiresAt = new Date(Date.now() + SESSION_DAYS * 864e5);
    await this.prisma.session.create({
      data: { userId: user.id, tokenHash: this.hashToken(token), expiresAt },
    });

    this.logger.log(`AUDIT login success ${user.id} from ${ip ?? 'unknown'}`);
    return { token, expiresAt, user: this.publicUser(user) };
  }

  async resolve(token?: string) {
    if (!token) return null;
    const session = await this.prisma.session.findUnique({
      where: { tokenHash: this.hashToken(token) },
      include: { user: true },
    });
    if (!session || session.expiresAt < new Date()) return null;
    if (session.user.status === UserStatus.SUSPENDED) return null;
    return session.user;
  }

  async logout(token?: string) {
    if (!token) return;
    await this.prisma.session
      .delete({ where: { tokenHash: this.hashToken(token) } })
      .catch(() => undefined);
  }

  /** Sessions accumulate as people log in; expired rows serve no purpose. */
  async purgeExpired() {
    const { count } = await this.prisma.session.deleteMany({
      where: { expiresAt: { lt: new Date() } },
    });
    return count;
  }

  publicUser(user: {
    id: string;
    email: string;
    name: string;
    role: string;
    status: string;
  }) {
    return {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.role,
      status: user.status,
    };
  }

  private dummyHashCache?: string;
  private async dummyHash() {
    this.dummyHashCache ??= await argon2.hash(randomBytes(32).toString('hex'));
    return this.dummyHashCache;
  }
}
