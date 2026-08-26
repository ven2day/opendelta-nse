import { expiredSessionCookie, isSecureRequest } from "../../server-auth";

export async function GET(request: Request): Promise<Response> {
  return new Response(null, {
    status: 303,
    headers: {
      Location: new URL("/login", request.url).toString(),
      "Set-Cookie": expiredSessionCookie(isSecureRequest(request)),
    },
  });
}
