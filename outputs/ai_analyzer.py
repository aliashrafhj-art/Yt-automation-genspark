import os
import json
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

VIRAL_ANALYSIS_PROMPT = """
You are a YouTube Shorts viral content expert. Analyze the following video transcript and identify the MOST viral and engaging segments suitable for YouTube Shorts (15-60 seconds each).

VIDEO TITLE: {title}
VIDEO DURATION: {duration} seconds

TRANSCRIPT (with timestamps):
{transcript}

HEATMAP DATA (most-replayed sections, higher value = more replayed):
{heatmap}

TASK: Identify {num_clips} viral segments for YouTube Shorts. Focus on:
1. 🔥 Strong hooks (surprising facts, controversies, emotional moments)
2. 💡 Valuable insights (tips, revelations, key takeaways)
3. 😂 Entertaining moments (funny, shocking, inspiring)
4. 🎯 Self-contained narratives (makes sense without context)
5. High-replay sections from heatmap data

Return a JSON array ONLY (no markdown, no explanation) with this structure:
[
  {{
    "rank": 1,
    "start": 120.5,
    "end": 175.0,
    "title": "Catchy short title",
    "hook": "Opening hook text (first 3 seconds)",
    "reason": "Why this is viral",
    "viral_score": 9.2,
    "category": "tip/funny/insight/story/controversy"
  }}
]

Rules:
- Each clip must be 30-90 seconds long
- Segments should NOT overlap
- Sort by viral_score descending
- Return EXACTLY {num_clips} segments
- start and end are in SECONDS (float)
"""


def analyze_with_gemini(
    title: str,
    duration: float,
    transcript: str,
    heatmap: list,
    num_clips: int = 5
) -> List[Dict]:
    """Use Google Gemini to find viral segments."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise Exception("GEMINI_API_KEY not set")
    
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    
    heatmap_str = json.dumps(heatmap[:50], indent=2) if heatmap else "No heatmap data available."
    
    prompt = VIRAL_ANALYSIS_PROMPT.format(
        title=title,
        duration=duration,
        transcript=transcript[:8000],  # Limit context
        heatmap=heatmap_str,
        num_clips=num_clips
    )
    
    model = genai.GenerativeModel("gemini-1.5-flash")
    logger.info("Sending to Gemini for viral analysis...")
    response = model.generate_content(prompt)
    
    text = response.text.strip()
    # Extract JSON
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    
    segments = json.loads(text)
    logger.info(f"Gemini found {len(segments)} viral segments.")
    return segments


def analyze_with_grok(
    title: str,
    duration: float,
    transcript: str,
    heatmap: list,
    num_clips: int = 5
) -> List[Dict]:
    """Use Grok AI to find viral segments."""
    api_key = os.getenv("GROK_API_KEY", "")
    if not api_key:
        raise Exception("GROK_API_KEY not set")
    
    from groq import Groq
    client = Groq(api_key=api_key)
    
    heatmap_str = json.dumps(heatmap[:50], indent=2) if heatmap else "No heatmap data available."
    
    prompt = VIRAL_ANALYSIS_PROMPT.format(
        title=title,
        duration=duration,
        transcript=transcript[:6000],
        heatmap=heatmap_str,
        num_clips=num_clips
    )
    
    logger.info("Sending to Grok for viral analysis...")
    chat = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama3-70b-8192",  # Grok via Groq API
        temperature=0.3
    )
    
    text = chat.choices[0].message.content.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    
    segments = json.loads(text)
    logger.info(f"Grok found {len(segments)} viral segments.")
    return segments


def fallback_heatmap_segments(heatmap: list, duration: float, num_clips: int = 5) -> List[Dict]:
    """
    If AI analysis fails, use YouTube heatmap to find top segments.
    Find peaks in heatmap data.
    """
    if not heatmap:
        # No heatmap: split video into equal parts
        clip_duration = min(60, duration / num_clips)
        segments = []
        for i in range(num_clips):
            start = i * (duration / num_clips)
            end = start + clip_duration
            segments.append({
                "rank": i + 1,
                "start": round(start, 1),
                "end": round(end, 1),
                "title": f"Segment {i+1}",
                "hook": "Check this out!",
                "reason": "Equal distribution fallback",
                "viral_score": 5.0,
                "category": "general"
            })
        return segments
    
    # Sort heatmap by value descending
    sorted_hm = sorted(heatmap, key=lambda x: x["value"], reverse=True)
    
    used_times = []
    segments = []
    rank = 1
    
    for point in sorted_hm:
        if rank > num_clips:
            break
        start = point["start"]
        end = start + 60  # 60 second window
        end = min(end, duration)
        
        # Check overlap with already selected segments
        overlap = False
        for us, ue in used_times:
            if not (end <= us or start >= ue):
                overlap = True
                break
        
        if not overlap and (end - start) >= 20:
            used_times.append((start, end))
            segments.append({
                "rank": rank,
                "start": round(start, 1),
                "end": round(end, 1),
                "title": f"Most Replayed Moment #{rank}",
                "hook": "You won't believe this!",
                "reason": f"High replay value: {point['value']:.2f}",
                "viral_score": round(point["value"] * 10, 1),
                "category": "highlight"
            })
            rank += 1
    
    return segments


def merge_and_rank_segments(gemini_segs: list, grok_segs: list) -> List[Dict]:
    """Merge results from Gemini and Grok, rank by combined score."""
    all_segments = {}
    
    for seg in gemini_segs:
        key = round(seg["start"])
        if key not in all_segments:
            all_segments[key] = seg.copy()
            all_segments[key]["gemini_score"] = seg.get("viral_score", 5.0)
            all_segments[key]["grok_score"] = 0
        else:
            all_segments[key]["gemini_score"] = seg.get("viral_score", 5.0)
    
    for seg in grok_segs:
        key = round(seg["start"])
        # Find nearest key within 15 seconds
        matched = None
        for k in all_segments:
            if abs(k - key) < 15:
                matched = k
                break
        if matched:
            all_segments[matched]["grok_score"] = seg.get("viral_score", 5.0)
        else:
            all_segments[key] = seg.copy()
            all_segments[key]["grok_score"] = seg.get("viral_score", 5.0)
            all_segments[key]["gemini_score"] = 0
    
    # Combined score
    result = []
    for key, seg in all_segments.items():
        g_score = seg.get("gemini_score", 0)
        r_score = seg.get("grok_score", 0)
        if g_score > 0 and r_score > 0:
            seg["viral_score"] = (g_score * 0.5 + r_score * 0.5)
        else:
            seg["viral_score"] = max(g_score, r_score)
        result.append(seg)
    
    result.sort(key=lambda x: x["viral_score"], reverse=True)
    for i, seg in enumerate(result):
        seg["rank"] = i + 1
    
    return result


def generate_ai_metadata(title: str, hook: str, reason: str) -> Dict:
    """Generate title, description, hashtags for a Short using Gemini."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {
            "title": title[:100],
            "description": f"{title}\n\n{reason}",
            "hashtags": "#shorts #viral #trending"
        }
    
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    
    prompt = f"""Generate YouTube Shorts metadata for this clip:
Title hint: {title}
Hook: {hook}
Why viral: {reason}

Return JSON only:
{{
  "title": "Catchy title under 100 chars with emoji",
  "description": "Engaging description 2-3 sentences + call to action",
  "hashtags": "#shorts #viral ... (10-15 relevant hashtags)"
}}"""
    
    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(prompt)
    text = response.text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    
    try:
        return json.loads(text)
    except Exception:
        return {
            "title": title[:100],
            "description": reason,
            "hashtags": "#shorts #viral #trending #youtube"
        }
