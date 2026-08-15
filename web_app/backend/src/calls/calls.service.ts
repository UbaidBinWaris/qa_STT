import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { CallStatus } from '@prisma/client';
import { createHash } from 'node:crypto';
import { extname } from 'node:path';
import { PrismaService } from '../prisma/prisma.service';
import { StorageService } from '../storage/storage.service';
import { BotsService } from '../bots/bots.service';
import { WorkerService } from '../worker/worker.service';

const ALLOWED_EXT = new Set([
  '.mp3', '.wav', '.m4a', '.flac', '.ogg', '.opus', '.webm', '.aac',
]);
const MAX_BYTES = 500 * 1024 * 1024;

@Injectable()
export class CallsService {
  private readonly logger = new Logger(CallsService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly storage: StorageService,
    private readonly bots: BotsService,
    private readonly worker: WorkerService,
  ) {}

  async upload(params: {
    userId: string;
    botId: string;
    filename: string;
    buffer: Buffer;
    mimetype?: string;
    publicBaseUrl: string;
  }) {
    const { userId, botId, filename, buffer } = params;

    const ext = extname(filename).toLowerCase();
    if (!ALLOWED_EXT.has(ext)) {
      throw new BadRequestException(
        `Unsupported format '${ext || 'none'}'. Accepted: ${[...ALLOWED_EXT].join(', ')}`,
      );
    }
    if (!buffer.length) throw new BadRequestException('File is empty');
    if (buffer.length > MAX_BYTES) {
      throw new BadRequestException(
        `File exceeds the ${MAX_BYTES / 1024 / 1024} MB limit`,
      );
    }

    await this.bots.assertOwned(botId, userId);

    // Deduplicate before spending quota: the same recording submitted twice
    // returns the original rather than costing the client a second call.
    const sha256 = createHash('sha256').update(buffer).digest('hex');
    const existing = await this.prisma.call.findUnique({
      where: { ownerId_sha256: { ownerId: userId, sha256 } },
    });
    if (existing) {
      this.logger.log(`duplicate upload by ${userId} -> ${existing.id}`);
      return { call: existing, duplicate: true };
    }

    const quota = await this.bots.checkQuota(botId);
    if (!quota.allowed) throw new ForbiddenException(quota.reason);

    const call = await this.prisma.call.create({
      data: {
        ownerId: userId,
        botId,
        filename,
        sizeBytes: buffer.length,
        sha256,
        status: CallStatus.QUEUED,
      },
    });

    const key = this.storage.originalKey(call.id, ext);
    try {
      await this.storage.putObject(key, buffer, params.mimetype);
    } catch (err) {
      // Storage failed, so the row describes a recording that does not exist.
      // Remove it rather than leaving a call that can never be processed.
      await this.prisma.call.delete({ where: { id: call.id } }).catch(() => {});
      this.logger.error(`storage write failed for ${call.id}: ${err}`);
      throw new BadRequestException('Could not store the recording. Try again.');
    }

    const updated = await this.prisma.call.update({
      where: { id: call.id },
      data: { originalKey: key },
    });

    await this.bots.consume(botId, 0);

    const dispatched = await this.worker.dispatch({
      callId: call.id,
      objectKey: key,
      callbackUrl: `${params.publicBaseUrl}/api/worker/progress`,
    });
    if (!dispatched) {
      await this.prisma.call.update({
        where: { id: call.id },
        data: { stage: 'waiting for worker' },
      });
    }

    return { call: updated, duplicate: false };
  }

  async listForUser(userId: string) {
    return this.prisma.call.findMany({
      where: { ownerId: userId },
      orderBy: { createdAt: 'desc' },
      select: {
        id: true, filename: true, durationSeconds: true, status: true,
        stage: true, progress: true, score: true, reliabilityScore: true,
        createdAt: true, completedAt: true, error: true, botId: true,
      },
    });
  }

  async get(callId: string, userId: string, isAdmin: boolean) {
    const call = await this.prisma.call.findUnique({ where: { id: callId } });
    if (!call) throw new NotFoundException('Call not found');
    if (!isAdmin && call.ownerId !== userId) {
      // Deliberately the same error as a missing call: a client must not be able
      // to discover that another client's recording exists.
      throw new NotFoundException('Call not found');
    }
    return call;
  }

  async audioUrl(callId: string, userId: string, isAdmin: boolean) {
    const call = await this.get(callId, userId, isAdmin);
    const key = call.audioKey ?? call.originalKey;
    if (!key) throw new NotFoundException('No audio stored for this call');
    return this.storage.presignedGet(key);
  }

  async remove(callId: string, userId: string, isAdmin: boolean) {
    const call = await this.get(callId, userId, isAdmin);
    await this.storage.removePrefix(`recordings/${call.id}/`).catch(() => 0);
    await this.storage.removePrefix(`derived/${call.id}/`).catch(() => 0);
    await this.prisma.call.delete({ where: { id: call.id } });
    this.logger.log(`AUDIT call ${call.id} deleted by ${userId}`);
    return { deleted: call.id };
  }

  /** Called by the Python worker. It cannot write to Postgres itself. */
  async applyProgress(report: {
    callId: string;
    status?: string;
    stage?: string;
    progress?: number;
    error?: string | null;
    durationSeconds?: number;
    result?: {
      score?: number;
      reliabilityScore?: number;
      transcript?: unknown;
      metrics?: unknown;
      qa?: unknown;
      reliability?: unknown;
      prosody?: unknown;
    };
  }) {
    const call = await this.prisma.call.findUnique({
      where: { id: report.callId },
    });
    if (!call) throw new NotFoundException('Unknown call');

    const status = report.status?.toUpperCase();
    const data: Record<string, unknown> = {};
    if (status && status in CallStatus) data.status = status as CallStatus;
    if (report.stage !== undefined) data.stage = report.stage;
    if (report.progress !== undefined) data.progress = report.progress;
    if (report.error !== undefined) data.error = report.error;
    if (report.durationSeconds !== undefined) {
      data.durationSeconds = report.durationSeconds;
    }
    if (report.result) {
      const r = report.result;
      if (r.score !== undefined) data.score = r.score;
      if (r.reliabilityScore !== undefined) data.reliabilityScore = r.reliabilityScore;
      for (const field of ['transcript', 'metrics', 'qa', 'reliability', 'prosody'] as const) {
        if (r[field] !== undefined) data[field] = r[field];
      }
    }
    if (status === 'COMPLETED') data.completedAt = new Date();

    const updated = await this.prisma.call.update({
      where: { id: call.id },
      data,
    });

    // Minutes are charged once the true duration is known, which the worker only
    // learns after decoding. The call itself was counted at upload.
    if (status === 'COMPLETED' && call.botId && report.durationSeconds) {
      await this.prisma.botUsage
        .update({
          where: {
            botId_day: {
              botId: call.botId,
              day: new Date(
                Date.UTC(
                  new Date().getUTCFullYear(),
                  new Date().getUTCMonth(),
                  new Date().getUTCDate(),
                ),
              ),
            },
          },
          data: {
            minutesProcessed: { increment: Math.ceil(report.durationSeconds / 60) },
          },
        })
        .catch(() => undefined);
    }

    return updated;
  }
}
