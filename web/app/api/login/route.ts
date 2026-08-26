import {
  authenticate,
  createSessionToken,
  isAuthConfigured,
  isSecureRequest,
  sessionCookie,
} from "../../server-auth";

export async function POST(request: Request): Promise<Response> {
  const form = await request.formData();
  const username = String(form.get("username") ?? "");
  const password = String(form.get("password") ?? "");
  const loginUrl = new URL("/login", request.url);

  if (!isAuthConfigured()) {
    loginUrl.searchParams.set("error", "configuration");
    return Response.redirect(loginUrl, 303);
  }

  if (!(await authenticate(username, password))) {
    loginUrl.searchParams.set("error", "invalid");
    return Response.redirect(loginUrl, 303);
  }

  const token = await createSessionToken(username.trim());
  return new Response(null, {
    status: 303,
    headers: {
      Location: new URL("/", request.url).toString(),
      "Set-Cookie": sessionCookie(token, isSecureRequest(request)),
    },
  });
}
