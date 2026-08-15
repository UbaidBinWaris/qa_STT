import { ValidationPipe } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import cookieParser from 'cookie-parser';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  app.use(cookieParser());

  // The three front ends are separate origins in development, and the session
  // cookie has to travel with every request — so credentials are on and the
  // origin list is explicit. A wildcard is not permitted with credentials, and
  // would let any site read a logged-in user's calls.
  const origins = (process.env.ALLOWED_ORIGINS ?? '')
    .split(',')
    .map((o) => o.trim())
    .filter(Boolean);
  app.enableCors({
    origin: origins.length
      ? origins
      : [
          'http://localhost:5000',
          'http://localhost:5001',
          'http://localhost:5002',
        ],
    credentials: true,
  });

  app.useGlobalPipes(new ValidationPipe({ transform: true, whitelist: true }));

  const port = Number(process.env.PORT_BACKEND ?? 5003);
  await app.listen(port);
  console.log(`ASCRAS backend listening on http://localhost:${port}`);
}
void bootstrap();
