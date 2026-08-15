import { ForbiddenException, Injectable, NotFoundException } from '@nestjs/common';
import { Bot, BotStatus } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

export interface QuotaVerdict {
  allowed: boolean;
  reason?: string;
  callsRemaining?: number | null;
  minutesRemaining?: number | null;
}

/**
 * Bots are the unit of capacity. An admin decides what each client's bot may do
 * and how much of it, and every upload is checked against that before any GPU
 * time is spent.
 */
@Injectable()
export class BotsService {
  constructor(private readonly prisma: PrismaService) {}

  /** The day boundary is the bot's own timezone, not the server's. A client in
   * Karachi should get a fresh allowance at their midnight, not at UTC's. */
  private dayFor(bot: Bot): Date {
    const local = new Date(
      new Date().toLocaleString('en-US', { timeZone: bot.timezone || 'UTC' }),
    );
    return new Date(Date.UTC(local.getFullYear(), local.getMonth(), local.getDate()));
  }

  private minutesSinceMidnight(bot: Bot): number {
    const local = new Date(
      new Date().toLocaleString('en-US', { timeZone: bot.timezone || 'UTC' }),
    );
    return local.getHours() * 60 + local.getMinutes();
  }

  private withinWindow(bot: Bot): boolean {
    const { windowStartMinute: start, windowEndMinute: end } = bot;
    if (start == null || end == null) return true;
    const now = this.minutesSinceMidnight(bot);
    // A window may wrap past midnight (e.g. 22:00-06:00), which is exactly the
    // off-hours case this exists for.
    return start <= end ? now >= start && now < end : now >= start || now < end;
  }

  async checkQuota(botId: string, durationSeconds = 0): Promise<QuotaVerdict> {
    const bot = await this.prisma.bot.findUnique({ where: { id: botId } });
    if (!bot) return { allowed: false, reason: 'Bot not found' };
    if (bot.status !== BotStatus.ENABLED) {
      return { allowed: false, reason: 'This bot is disabled' };
    }
    if (!this.withinWindow(bot)) {
      return {
        allowed: false,
        reason: `This bot only runs between ${this.fmt(bot.windowStartMinute!)} and ${this.fmt(bot.windowEndMinute!)} (${bot.timezone})`,
      };
    }

    const day = this.dayFor(bot);
    const usage = await this.prisma.botUsage.findUnique({
      where: { botId_day: { botId, day } },
    });
    const usedCalls = usage?.callsProcessed ?? 0;
    const usedMinutes = usage?.minutesProcessed ?? 0;
    const wanted = Math.ceil(durationSeconds / 60);

    if (bot.dailyCallLimit != null && usedCalls >= bot.dailyCallLimit) {
      return {
        allowed: false,
        reason: `Daily limit of ${bot.dailyCallLimit} calls reached`,
        callsRemaining: 0,
      };
    }
    if (
      bot.dailyMinuteLimit != null &&
      usedMinutes + wanted > bot.dailyMinuteLimit
    ) {
      return {
        allowed: false,
        reason: `Daily limit of ${bot.dailyMinuteLimit} minutes reached`,
        minutesRemaining: Math.max(0, bot.dailyMinuteLimit - usedMinutes),
      };
    }

    return {
      allowed: true,
      callsRemaining:
        bot.dailyCallLimit == null ? null : bot.dailyCallLimit - usedCalls,
      minutesRemaining:
        bot.dailyMinuteLimit == null ? null : bot.dailyMinuteLimit - usedMinutes,
    };
  }

  /** Counted when work is accepted, not when it finishes — otherwise a burst of
   * uploads all pass the check before any of them increments the counter. */
  async consume(botId: string, durationSeconds: number) {
    const bot = await this.prisma.bot.findUnique({ where: { id: botId } });
    if (!bot) return;
    const day = this.dayFor(bot);
    const minutes = Math.ceil(durationSeconds / 60);
    await this.prisma.botUsage.upsert({
      where: { botId_day: { botId, day } },
      create: { botId, day, callsProcessed: 1, minutesProcessed: minutes },
      update: {
        callsProcessed: { increment: 1 },
        minutesProcessed: { increment: minutes },
      },
    });
  }

  async assertOwned(botId: string, userId: string) {
    const bot = await this.prisma.bot.findUnique({ where: { id: botId } });
    if (!bot) throw new NotFoundException('Bot not found');
    if (bot.ownerId !== userId) throw new ForbiddenException('Not your bot');
    return bot;
  }

  async listForUser(userId: string) {
    const bots = await this.prisma.bot.findMany({
      where: { ownerId: userId },
      orderBy: { createdAt: 'asc' },
    });
    return Promise.all(
      bots.map(async (bot) => ({
        ...bot,
        quota: await this.checkQuota(bot.id),
      })),
    );
  }

  private fmt(minute: number) {
    const h = String(Math.floor(minute / 60)).padStart(2, '0');
    const m = String(minute % 60).padStart(2, '0');
    return `${h}:${m}`;
  }
}
