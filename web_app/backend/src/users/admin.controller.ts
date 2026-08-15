import {
  Body,
  Controller,
  Delete,
  Get,
  Logger,
  NotFoundException,
  Param,
  Patch,
  Post,
  Req,
} from '@nestjs/common';
import { BotStatus, UserRole, UserStatus } from '@prisma/client';
import { z } from 'zod';
import { AuthService } from '../auth/auth.service';
import type { AuthedRequest } from '../auth/auth.guard';
import { Roles } from '../auth/auth.guard';
import { PrismaService } from '../prisma/prisma.service';

const CreateUser = z.object({
  email: z.string().email(),
  name: z.string().min(1).max(120),
  password: z.string().min(8).max(200),
  role: z.nativeEnum(UserRole).optional(),
  contactChannel: z.string().max(40).optional(),
  contactHandle: z.string().max(200).optional(),
});

const CreateBot = z.object({
  ownerId: z.string(),
  name: z.string().min(1).max(120),
  status: z.nativeEnum(BotStatus).optional(),
  dailyCallLimit: z.number().int().positive().nullable().optional(),
  dailyMinuteLimit: z.number().int().positive().nullable().optional(),
  windowStartMinute: z.number().int().min(0).max(1439).nullable().optional(),
  windowEndMinute: z.number().int().min(0).max(1439).nullable().optional(),
  timezone: z.string().max(64).optional(),
  canTranscribe: z.boolean().optional(),
  canRunQa: z.boolean().optional(),
  canExport: z.boolean().optional(),
});

const RecordPayment = z.object({
  userId: z.string(),
  amount: z.number().positive(),
  currency: z.string().length(3).optional(),
  method: z.string().max(60).optional(),
  reference: z.string().max(200).optional(),
  note: z.string().max(2000).optional(),
  periodStart: z.string().datetime().optional(),
  periodEnd: z.string().datetime().optional(),
});

/**
 * Everything an operator does by hand: create accounts, hand out bots with
 * limits, and record payments that happened elsewhere. There is no self-serve
 * path into any of this.
 */
@Roles(UserRole.ADMIN)
@Controller('api/admin')
export class AdminController {
  private readonly logger = new Logger(AdminController.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly auth: AuthService,
  ) {}

  private async audit(req: AuthedRequest, action: string, target?: string, metadata?: unknown) {
    await this.prisma.auditLog.create({
      data: {
        actorId: req.user?.id,
        action,
        target,
        metadata: metadata as never,
        ip: req.ip,
      },
    });
  }

  // ---------- users ----------

  @Get('users')
  async users() {
    const users = await this.prisma.user.findMany({
      orderBy: { createdAt: 'desc' },
      include: {
        _count: { select: { calls: true, bots: true } },
        payments: { select: { amount: true } },
      },
    });
    return users.map((u) => ({
      id: u.id,
      email: u.email,
      name: u.name,
      role: u.role,
      status: u.status,
      contactChannel: u.contactChannel,
      contactHandle: u.contactHandle,
      calls: u._count.calls,
      bots: u._count.bots,
      paidTotal: u.payments.reduce((sum, p) => sum + Number(p.amount), 0),
      createdAt: u.createdAt,
    }));
  }

  @Post('users')
  async createUser(@Req() req: AuthedRequest, @Body() body: unknown) {
    const data = CreateUser.parse(body);
    const user = await this.prisma.user.create({
      data: {
        email: data.email.toLowerCase(),
        name: data.name,
        passwordHash: await this.auth.hashPassword(data.password),
        role: data.role ?? UserRole.CLIENT,
        contactChannel: data.contactChannel,
        contactHandle: data.contactHandle,
      },
    });
    await this.audit(req, 'user.create', user.id, { email: user.email });
    this.logger.log(`AUDIT user ${user.email} created by ${req.user?.email}`);
    return this.auth.publicUser(user);
  }

  @Patch('users/:id')
  async updateUser(
    @Req() req: AuthedRequest,
    @Param('id') id: string,
    @Body() body: { status?: UserStatus; name?: string; password?: string },
  ) {
    const data: Record<string, unknown> = {};
    if (body.status) data.status = body.status;
    if (body.name) data.name = body.name;
    if (body.password) data.passwordHash = await this.auth.hashPassword(body.password);

    const user = await this.prisma.user.update({ where: { id }, data });
    // Suspending must take effect immediately, not whenever the cookie expires.
    if (body.status === UserStatus.SUSPENDED) {
      await this.prisma.session.deleteMany({ where: { userId: id } });
    }
    await this.audit(req, 'user.update', id, body.password ? { passwordReset: true } : body);
    return this.auth.publicUser(user);
  }

  // ---------- bots ----------

  @Get('bots')
  bots() {
    return this.prisma.bot.findMany({
      orderBy: { createdAt: 'desc' },
      include: {
        owner: { select: { id: true, email: true, name: true } },
        _count: { select: { calls: true } },
      },
    });
  }

  @Post('bots')
  async createBot(@Req() req: AuthedRequest, @Body() body: unknown) {
    const data = CreateBot.parse(body);
    const bot = await this.prisma.bot.create({ data });
    await this.audit(req, 'bot.create', bot.id, { owner: data.ownerId });
    return bot;
  }

  @Patch('bots/:id')
  async updateBot(@Req() req: AuthedRequest, @Param('id') id: string, @Body() body: unknown) {
    const data = CreateBot.partial().omit({ ownerId: true }).parse(body);
    const bot = await this.prisma.bot.update({ where: { id }, data });
    await this.audit(req, 'bot.update', id, data);
    return bot;
  }

  @Delete('bots/:id')
  async deleteBot(@Req() req: AuthedRequest, @Param('id') id: string) {
    const calls = await this.prisma.call.count({ where: { botId: id } });
    if (calls > 0) {
      // The schema enforces this too; answering clearly here beats surfacing a
      // raw foreign-key error.
      throw new NotFoundException(
        `This bot has processed ${calls} call(s) and cannot be deleted. Disable it instead.`,
      );
    }
    await this.prisma.bot.delete({ where: { id } });
    await this.audit(req, 'bot.delete', id);
    return { deleted: id };
  }

  @Get('bots/:id/usage')
  usage(@Param('id') id: string) {
    return this.prisma.botUsage.findMany({
      where: { botId: id },
      orderBy: { day: 'desc' },
      take: 30,
    });
  }

  // ---------- payments ----------

  @Get('payments')
  payments() {
    return this.prisma.payment.findMany({
      orderBy: { createdAt: 'desc' },
      include: { user: { select: { id: true, email: true, name: true } } },
    });
  }

  @Post('payments')
  async recordPayment(@Req() req: AuthedRequest, @Body() body: unknown) {
    const data = RecordPayment.parse(body);
    const payment = await this.prisma.payment.create({
      data: {
        userId: data.userId,
        amount: data.amount,
        currency: data.currency ?? 'USD',
        method: data.method,
        reference: data.reference,
        note: data.note,
        periodStart: data.periodStart ? new Date(data.periodStart) : undefined,
        periodEnd: data.periodEnd ? new Date(data.periodEnd) : undefined,
        recordedById: req.user?.id,
      },
    });
    await this.audit(req, 'payment.record', payment.id, {
      userId: data.userId,
      amount: data.amount,
    });
    return payment;
  }

  // ---------- overview ----------

  @Get('stats')
  async stats() {
    const [users, bots, calls, completed, failed, payments] = await Promise.all([
      this.prisma.user.count(),
      this.prisma.bot.count(),
      this.prisma.call.count(),
      this.prisma.call.count({ where: { status: 'COMPLETED' } }),
      this.prisma.call.count({ where: { status: 'FAILED' } }),
      this.prisma.payment.aggregate({ _sum: { amount: true } }),
    ]);
    return {
      users,
      bots,
      calls,
      completed,
      failed,
      revenue: Number(payments._sum.amount ?? 0),
    };
  }

  @Get('audit')
  auditLog() {
    return this.prisma.auditLog.findMany({
      orderBy: { createdAt: 'desc' },
      take: 200,
    });
  }
}
