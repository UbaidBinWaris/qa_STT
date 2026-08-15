import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

/**
 * Bridge to the Python GPU service.
 *
 * The worker holds no database credentials. NestJS hands it a job and a callback
 * URL; the worker reports progress back over HTTP and NestJS is the only writer.
 * That keeps one owner for the data, and means the GPU box can be restarted,
 * moved or rebuilt without Postgres knowing or caring.
 */
@Injectable()
export class WorkerService {
  private readonly logger = new Logger(WorkerService.name);

  constructor(private readonly config: ConfigService) {}

  private get baseUrl() {
    return (
      this.config.get<string>('PYTHON_WORKER_URL') ?? 'http://localhost:8000'
    );
  }

  async health(): Promise<{ reachable: boolean; detail?: unknown }> {
    try {
      const res = await fetch(`${this.baseUrl}/api/worker/health`, {
        signal: AbortSignal.timeout(4000),
        headers: this.workerHeaders(),
      });
      if (!res.ok) return { reachable: false, detail: `HTTP ${res.status}` };
      return { reachable: true, detail: await res.json() };
    } catch (err) {
      return { reachable: false, detail: String(err) };
    }
  }

  /**
   * Hand a stored recording to the GPU service. Returns false rather than
   * throwing when the worker is down: the call stays queued and can be retried,
   * which is preferable to losing an upload the client already made.
   */
  async dispatch(job: {
    callId: string;
    objectKey: string;
    callbackUrl: string;
  }): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/api/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...this.workerHeaders() },
        body: JSON.stringify(job),
        signal: AbortSignal.timeout(10_000),
      });
      if (!res.ok) {
        this.logger.error(`worker rejected job ${job.callId}: HTTP ${res.status}`);
        return false;
      }
      return true;
    } catch (err) {
      this.logger.error(`worker unreachable for job ${job.callId}: ${err}`);
      return false;
    }
  }

  /** Shared secret both directions, so neither side accepts orders from anyone
   * who merely reached the port. */
  workerHeaders(): Record<string, string> {
    const secret = this.config.get<string>('WORKER_CALLBACK_SECRET');
    return secret ? { 'x-worker-secret': secret } : {};
  }

  verifySecret(provided?: string): boolean {
    const expected = this.config.get<string>('WORKER_CALLBACK_SECRET');
    if (!expected) return true; // not configured: local development
    return !!provided && provided === expected;
  }
}
