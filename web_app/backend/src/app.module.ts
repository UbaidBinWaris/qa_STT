import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { APP_GUARD } from '@nestjs/core';
import { AuthGuard } from './auth/auth.guard';
import { AuthModule } from './auth/auth.module';
import { BotsModule } from './bots/bots.module';
import { CallsModule } from './calls/calls.module';
import { HealthController } from './health.controller';
import { PrismaModule } from './prisma/prisma.module';
import { StorageModule } from './storage/storage.module';
import { UsersModule } from './users/users.module';
import { WorkerModule } from './worker/worker.module';

@Module({
  imports: [
    // The repository root .env is the single source of configuration, shared
    // with the Python worker and the backup scripts.
    ConfigModule.forRoot({ isGlobal: true, envFilePath: ['../../.env', '.env'] }),
    PrismaModule,
    StorageModule,
    AuthModule,
    BotsModule,
    WorkerModule,
    CallsModule,
    UsersModule,
  ],
  controllers: [HealthController],
  providers: [
    // Global: endpoints are protected unless they opt out with @Public().
    { provide: APP_GUARD, useClass: AuthGuard },
  ],
})
export class AppModule {}
