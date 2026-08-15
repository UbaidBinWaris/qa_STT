import { NextResponse, type NextRequest } from "next/server";

/**
 * Cheap gate only: if there is no session cookie at all, go straight to login
 * rather than flashing an empty dashboard. Whether the cookie is *valid* is
 * decided by the backend on every API call — this never grants access, it only
 * avoids a pointless round trip.
 */
export function middleware(req: NextRequest) {
  const hasSession = req.cookies.has("ascras_session");
  const isLogin = req.nextUrl.pathname.startsWith("/login");

  if (!hasSession && !isLogin) {
    return NextResponse.redirect(new URL("/login", req.url));
  }
  if (hasSession && isLogin) {
    return NextResponse.redirect(new URL("/", req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next|favicon.ico).*)"],
};
