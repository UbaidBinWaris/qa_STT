import { Controller, Get, Req } from '@nestjs/common';
import type { AuthedRequest } from '../auth/auth.guard';
import { BotsService } from './bots.service';

@Controller('api/bots')
export class BotsController {
  constructor(private readonly bots: BotsService) {}

  /** A client sees only their own bots, with live remaining quota. */
  @Get()
  mine(@Req() req: AuthedRequest) {
    return this.bots.listForUser(req.user!.id);
  }
}
