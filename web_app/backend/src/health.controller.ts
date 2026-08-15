import { Controller, Get } from '@nestjs/common';
import { Public } from './auth/auth.guard';
import { PrismaService } from './prisma/prisma.service';
import { WorkerService } from './worker/worker.service';

@Controller('api')
export class HealthController {
  constructor(
    private readonly prisma: PrismaService,
    private readonly worker: WorkerService,
  ) {}

  /** Public on purpose: a load balancer or an operator needs this without a
   * session, and it exposes no customer data. */
  @Public()
  @Get('health')
  async health() {
    const db = await this.prisma
      .$queryRaw`SELECT 1`
      .then(() => true)
      .catch(() => false);
    const worker = await this.worker.health();
    return {
      status: db ? 'healthy' : 'degraded',
      database: db,
      worker: worker.reachable,
      // Surface the reason on failure too. A bare `false` gives an operator
      // nothing to act on, which is exactly when they need the detail most.
      workerDetail: worker.detail,
    };
  }
}
