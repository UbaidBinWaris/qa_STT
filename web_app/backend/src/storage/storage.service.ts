import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Client as MinioClient } from 'minio';

/**
 * Object storage for call recordings.
 *
 * Audio never touches Postgres — the database holds keys, MinIO holds bytes.
 * Nothing here is ever public: the portal plays audio through short-lived
 * presigned URLs, so a recording is only reachable by someone who just proved
 * they are allowed to see it.
 */
@Injectable()
export class StorageService implements OnModuleInit {
  private readonly logger = new Logger(StorageService.name);
  private client!: MinioClient;
  private bucket!: string;

  constructor(private readonly config: ConfigService) {}

  async onModuleInit() {
    const endpoint = new URL(
      this.config.get<string>('MINIO_ENDPOINT') ?? 'http://localhost:9000',
    );
    this.bucket = this.config.get<string>('MINIO_BUCKET') ?? 'qa-stt';

    this.client = new MinioClient({
      endPoint: endpoint.hostname,
      port: Number(endpoint.port) || (endpoint.protocol === 'https:' ? 443 : 80),
      useSSL: endpoint.protocol === 'https:',
      accessKey: this.config.get<string>('MINIO_ROOT_USER') ?? '',
      secretKey: this.config.get<string>('MINIO_ROOT_PASSWORD') ?? '',
    });

    try {
      const exists = await this.client.bucketExists(this.bucket);
      if (!exists) {
        await this.client.makeBucket(this.bucket);
        this.logger.log(`created bucket ${this.bucket}`);
      }
      this.logger.log(`storage ready: ${endpoint.host}/${this.bucket}`);
    } catch (err) {
      // A dead object store must not stop the API booting — uploads will fail
      // loudly and everything else (login, admin, reading past results) works.
      this.logger.error(`storage unreachable at ${endpoint.host}: ${err}`);
    }
  }

  /** `recordings/<callId>/original.<ext>` — the client's upload, never mutated. */
  originalKey(callId: string, ext: string) {
    return `recordings/${callId}/original${ext.startsWith('.') ? ext : `.${ext}`}`;
  }

  /** `derived/<callId>/audio.wav` — what the pipeline actually reads. */
  derivedKey(callId: string, name: string) {
    return `derived/${callId}/${name}`;
  }

  async putObject(key: string, body: Buffer, contentType?: string) {
    await this.client.putObject(this.bucket, key, body, body.length, {
      'Content-Type': contentType ?? 'application/octet-stream',
    });
    return key;
  }

  async getObject(key: string): Promise<Buffer> {
    const stream = await this.client.getObject(this.bucket, key);
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(chunk as Buffer);
    return Buffer.concat(chunks);
  }

  /**
   * Time-limited URL for playback. Ten minutes is long enough to listen to a
   * long call and short enough that a URL pasted into a chat stops working.
   */
  async presignedGet(key: string, expirySeconds = 600) {
    return this.client.presignedGetObject(this.bucket, key, expirySeconds);
  }

  async removePrefix(prefix: string) {
    const keys: string[] = [];
    const stream = this.client.listObjectsV2(this.bucket, prefix, true);
    for await (const obj of stream) {
      if (obj.name) keys.push(obj.name);
    }
    if (keys.length) await this.client.removeObjects(this.bucket, keys);
    return keys.length;
  }
}
