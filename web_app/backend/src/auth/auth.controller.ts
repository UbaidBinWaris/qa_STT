import {
  Body,
  Controller,
  Get,
  Post,
  Req,
  Res,
  UnauthorizedException,
} from '@nestjs/common';
import type { Request, Response } from 'express';
import { z } from 'zod';
import { AuthService, SESSION_COOKIE } from './auth.service';
import type { AuthedRequest } from './auth.guard';
import { Public } from './auth.guard';

const LoginSchema = z.object({
  email: z.string().email().max(200),
  password: z.string().min(1).max(512),
});

@Controller('api/auth')
export class AuthController {
  constructor(private readonly auth: AuthService) {}

  @Public()
  @Post('login')
  async login(
    @Body() body: unknown,
    @Req() req: Request,
    @Res({ passthrough: true }) res: Response,
  ) {
    const parsed = LoginSchema.safeParse(body);
    if (!parsed.success) throw new UnauthorizedException('Invalid credentials');

    const { token, expiresAt, user } = await this.auth.login(
      parsed.data.email,
      parsed.data.password,
      req.ip,
    );

    res.cookie(SESSION_COOKIE, token, {
      httpOnly: true,
      sameSite: 'lax',
      // Only over TLS in production; forcing it in local development would stop
      // the cookie being set at all over plain http.
      secure: process.env.NODE_ENV === 'production',
      expires: expiresAt,
      path: '/',
    });
    return { user };
  }

  @Post('logout')
  async logout(
    @Req() req: Request,
    @Res({ passthrough: true }) res: Response,
  ) {
    await this.auth.logout(req.cookies?.[SESSION_COOKIE]);
    res.clearCookie(SESSION_COOKIE, { path: '/' });
    return { ok: true };
  }

  @Get('me')
  me(@Req() req: AuthedRequest) {
    return { user: req.user };
  }
}
