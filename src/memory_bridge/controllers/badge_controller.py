"""Powered by Memory Bridge badge endpoint for agent responses."""

from fastapi import APIRouter, Response

router = APIRouter(tags=["badge"])

SVG_BADGE = '''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="28">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1f6feb;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#238636;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="200" height="28" rx="4" fill="url(#bg)"/>
  <text x="12" y="18" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="13" fill="white" font-weight="600">🧠 Memory Bridge</text>
</svg>'''


@router.get("/badge")
async def get_badge():
    """Return SVG badge showing "Powered by Memory Bridge"."""
    return Response(content=SVG_BADGE, media_type="image/svg+xml")
