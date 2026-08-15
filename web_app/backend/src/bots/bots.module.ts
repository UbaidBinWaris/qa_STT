import { Global, Module } from '@nestjs/common';
import { BotsController } from './bots.controller';
import { BotsService } from './bots.service';

@Global()
@Module({
  controllers: [BotsController],
  providers: [BotsService],
  exports: [BotsService],
})
export class BotsModule {}
