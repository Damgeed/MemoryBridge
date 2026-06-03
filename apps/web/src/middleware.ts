import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Memory Bridge uses custom JWT auth (client-side token in localStorage).
// This middleware only handles public asset paths.
// Auth enforcement is handled client-side via the auth provider.

const publicPaths = ["/", "/api/auth", "/_next/static", "/favicon.ico"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow public paths
  if (publicPaths.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
