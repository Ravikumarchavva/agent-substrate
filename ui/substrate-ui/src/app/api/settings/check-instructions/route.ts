/**
 * POST /api/settings/check-instructions
 * Validates custom system instructions against OpenAI Moderation API.
 * Returns { allowed: true } or { allowed: false, reason: string }.
 */
import { NextRequest, NextResponse } from "next/server";

const FLAGGED_PATTERNS: RegExp[] = [
  // Jailbreak attempts
  /ignore (all |previous |your )?instructions/i,
  /you are (now |a )?(?:DAN|jailbreak|unrestricted|uncensored)/i,
  /pretend (you have no|there are no) (restrictions|rules|guidelines)/i,
  /bypass (your|all) (restrictions|safety|filters)/i,
  /act as if you (are|were) (not|without) (an? )?AI/i,
];

export async function POST(req: NextRequest) {
  const body = await req.json();
  const instructions: unknown = body.instructions;

  if (typeof instructions !== "string") {
    return NextResponse.json({ allowed: false, reason: "Invalid input." }, { status: 400 });
  }

  const text = instructions.trim();

  if (!text) {
    return NextResponse.json({ allowed: true });
  }

  // 1. Structural pattern check (fast, no API call)
  for (const pattern of FLAGGED_PATTERNS) {
    if (pattern.test(text)) {
      return NextResponse.json({
        allowed: false,
        reason: "Refused to save prompt — jailbreak or policy-override attempt detected.",
      });
    }
  }

  // 2. OpenAI Moderation API (detects hate, harassment, violence, sexual content, etc.)
  const apiKey = process.env.OPENAI_API_KEY;
  if (apiKey) {
    try {
      const modRes = await fetch("https://api.openai.com/v1/moderations", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({ input: text }),
      });

      if (modRes.ok) {
        const modData = await modRes.json();
        const result = modData.results?.[0];

        if (result?.flagged) {
          const flaggedCategories: string[] = Object.entries(
            result.categories as Record<string, boolean>
          )
            .filter(([, v]) => v)
            .map(([k]) => k.replace(/_/g, " "));

          return NextResponse.json({
            allowed: false,
            reason: `Refused to save prompt — content policy raised: ${flaggedCategories.join(", ")}.`,
          });
        }
      }
    } catch (err) {
      console.error("[check-instructions] Moderation API error:", err);
      // Fail open — don't block saves if moderation API is unavailable
    }
  }

  return NextResponse.json({ allowed: true });
}
