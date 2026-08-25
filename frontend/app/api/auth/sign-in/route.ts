import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const { email, password } = await request.json().catch(() => ({}));
  if (typeof email !== "string" || typeof password !== "string") {
    return NextResponse.json({ error_description: "Email and password are required." }, { status: 400 });
  }

  const url = process.env.SUPABASE_URL;
  const apiKey = process.env.SUPABASE_ANON_KEY;
  if (!url || !apiKey) {
    return NextResponse.json({ error_description: "Sign-in is not configured." }, { status: 503 });
  }

  const response = await fetch(`${url}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: apiKey, "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const body = await response.json().catch(() => ({ error_description: "Sign-in failed." }));

  return NextResponse.json(body, { status: response.status });
}
