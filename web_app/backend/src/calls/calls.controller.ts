import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  Headers,
  Param,
  Post,
  Req,
  UnauthorizedException,
  UploadedFile,
  UseInterceptors,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { UserRole } from '@prisma/client';
import type { Request } from 'express';
import type { AuthedRequest } from '../auth/auth.guard';
import { Public } from '../auth/auth.guard';
import { WorkerService } from '../worker/worker.service';
import { CallsService } from './calls.service';

@Controller('api')
export class CallsController {
  constructor(
    private readonly calls: CallsService,
    private readonly worker: WorkerService,
  ) {}

  private baseUrl(req: Request) {
    return (
      process.env.BACKEND_PUBLIC_URL ??
      `${req.protocol}://${req.get('host') ?? 'localhost:5003'}`
    );
  }

  @Post('calls')
  @UseInterceptors(FileInterceptor('file', { limits: { fileSize: 500 * 1024 * 1024 } }))
  async upload(
    @Req() req: AuthedRequest,
    @UploadedFile() file: Express.Multer.File | undefined,
    @Body('botId') botId: string,
  ) {
    if (!file) throw new BadRequestException('No file uploaded');
    if (!botId) throw new BadRequestException('botId is required');

    const { call, duplicate } = await this.calls.upload({
      userId: req.user!.id,
      botId,
      filename: file.originalname,
      buffer: file.buffer,
      mimetype: file.mimetype,
      publicBaseUrl: this.baseUrl(req),
    });
    return { callId: call.id, status: call.status, duplicate, filename: call.filename };
  }

  @Get('calls')
  list(@Req() req: AuthedRequest) {
    return this.calls.listForUser(req.user!.id);
  }

  @Get('calls/:id')
  get(@Req() req: AuthedRequest, @Param('id') id: string) {
    return this.calls.get(id, req.user!.id, req.user!.role === UserRole.ADMIN);
  }

  @Get('calls/:id/audio-url')
  async audio(@Req() req: AuthedRequest, @Param('id') id: string) {
    const url = await this.calls.audioUrl(
      id,
      req.user!.id,
      req.user!.role === UserRole.ADMIN,
    );
    return { url };
  }

  @Delete('calls/:id')
  remove(@Req() req: AuthedRequest, @Param('id') id: string) {
    return this.calls.remove(id, req.user!.id, req.user!.role === UserRole.ADMIN);
  }

  /**
   * The Python worker's only way into the database. Authenticated by shared
   * secret rather than a session, because the worker is a machine and holds no
   * user credentials.
   */
  @Public()
  @Post('worker/progress')
  async progress(
    @Headers('x-worker-secret') secret: string | undefined,
    @Body() body: any,
  ) {
    if (!this.worker.verifySecret(secret)) {
      throw new UnauthorizedException('Bad worker secret');
    }
    if (!body?.callId) throw new BadRequestException('callId is required');
    const updated = await this.calls.applyProgress(body);
    return { ok: true, status: updated.status };
  }
}
