import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
  SetMetadata,
  UnauthorizedException,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { UserRole } from '@prisma/client';
import type { Request } from 'express';
import { AuthService, SESSION_COOKIE } from './auth.service';

export const PUBLIC_KEY = 'isPublic';
export const Public = () => SetMetadata(PUBLIC_KEY, true);

export const ROLES_KEY = 'roles';
export const Roles = (...roles: UserRole[]) => SetMetadata(ROLES_KEY, roles);

export interface AuthedRequest extends Request {
  user?: { id: string; email: string; name: string; role: UserRole };
}

/**
 * Applied globally, so a new controller is protected by default and has to opt
 * out with @Public(). The opposite arrangement leaks the first endpoint someone
 * forgets to annotate.
 */
@Injectable()
export class AuthGuard implements CanActivate {
  constructor(
    private readonly auth: AuthService,
    private readonly reflector: Reflector,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.getAllAndOverride<boolean>(PUBLIC_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;

    const req = context.switchToHttp().getRequest<AuthedRequest>();
    const token = req.cookies?.[SESSION_COOKIE];
    const user = await this.auth.resolve(token);
    if (!user) throw new UnauthorizedException('Authentication required');

    req.user = {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.role,
    };

    const required = this.reflector.getAllAndOverride<UserRole[]>(ROLES_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (required?.length && !required.includes(user.role)) {
      throw new ForbiddenException('Not permitted');
    }
    return true;
  }
}
