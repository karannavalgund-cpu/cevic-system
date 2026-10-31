from __future__ import annotations
import base64
import hashlib
import json
import os
import random
import string
import tempfile
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

load_dotenv()

# ── Optional dependencies ──────────────────────────────────────────────────────
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None
    WHISPER_AVAILABLE = False

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = bool(os.getenv("OPENAI_API_KEY"))
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) if OPENAI_AVAILABLE else None
except ImportError:
    OPENAI_AVAILABLE = False
    openai_client = None

# ── Supabase ───────────────────────────────────────────────────────────────────
try:
    from supabase import create_client
    SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wzlqorsyweibmknewxnt.supabase.co")
    # Use SERVICE ROLE key for backend (bypasses RLS). Store it in .env — never in client code.
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_URL and SUPABASE_SERVICE_KEY else None
    SUPABASE_AVAILABLE = supabase is not None
except Exception as e:
    print(f"[Supabase] Init failed: {e}")
    supabase = None
    SUPABASE_AVAILABLE = False

# ── Local fallback (in-memory) ─────────────────────────────────────────────────
LOCAL_DB: dict[str, dict[str, Any]] = {
    "complaints": {}, "timeline": {}, "assignments": {}, "resolution": {}, "feedback": {}
}

_whisper_model = None
def get_whisper_model():
    global _whisper_model
    if _whisper_model is None and WHISPER_AVAILABLE:
        _whisper_model = WhisperModel(
            os.getenv("WHISPER_MODEL_SIZE", "small"),
            device=os.getenv("WHISPER_DEVICE", "cpu"),
            compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
        )
    return _whisper_model

# ── Helpers ────────────────────────────────────────────────────────────────────
SECRET_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _generate_complaint_id() -> str:
    return f"TC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"

def _generate_secret() -> str:
    return "".join(random.choices(SECRET_ALPHABET, k=8))

def _normalize_secret(secret: str) -> str:
    return "".join((secret or "").strip().upper().replace("-", "").split())

def _normalize_complaint_id(complaint_id: str) -> str:
    return (complaint_id or "").strip().upper()

def _deadline(hours: int = 48) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

def _coerce_float(value: Any):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

# ── Auth tokens ────────────────────────────────────────────────────────────────
OFFICER_TOKEN = os.getenv("OFFICER_SHARED_TOKEN", "change-me")
CRON_TOKEN    = os.getenv("CRON_TOKEN", "change-me-cron")

def require_officer(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != OFFICER_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper

def require_cron(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != CRON_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper

# ── Department / officer mapping ───────────────────────────────────────────────
DEPARTMENT_MAP = {
    "roads": {
        "dept": "Roads & Infrastructure Department",
        "officers": [{"officer_id": "OFC-001", "name": "Ramesh Kumar", "phone": "+91-9900001111", "level": 1, "jurisdiction": "Ward 12", "escalation_label": "Ward Engineer"}],
    },
    "sewage": {
        "dept": "Sewage & Sanitation Department",
        "officers": [{"officer_id": "OFC-005", "name": "Suresh Naik", "phone": "+91-9900005555", "level": 1, "jurisdiction": "Zone 3", "escalation_label": "Zone Sanitation Officer"}],
    },
    "water_supply": {
        "dept": "Water Supply Department",
        "officers": [{"officer_id": "OFC-002", "name": "Anita Sharma", "phone": "+91-9900004444", "level": 1, "jurisdiction": "Zone 5", "escalation_label": "Zone Officer"}],
    },
    "electricity": {
        "dept": "Electricity Department",
        "officers": [{"officer_id": "OFC-003", "name": "Vijay Reddy", "phone": "+91-9900007777", "level": 1, "jurisdiction": "Section 7", "escalation_label": "Section Officer"}],
    },
    "solid_waste": {
        "dept": "Solid Waste Management Department",
        "officers": [{"officer_id": "OFC-006", "name": "Kavitha Gowda", "phone": "+91-9900006666", "level": 1, "jurisdiction": "Ward 12", "escalation_label": "Ward Health Inspector"}],
    },
    "parks": {
        "dept": "Parks & Horticulture Department",
        "officers": [{"officer_id": "OFC-007", "name": "Mohan Das", "phone": "+91-9900007788", "level": 1, "jurisdiction": "Zone 2", "escalation_label": "Zone Horticulture Officer"}],
    },
    "building_permit": {
        "dept": "Town Planning & Building Department",
        "officers": [{"officer_id": "OFC-008", "name": "Priya Hegde", "phone": "+91-9900008888", "level": 1, "jurisdiction": "Zone 4", "escalation_label": "Planning Inspector"}],
    },
    "health_sanitation": {
        "dept": "Health & Sanitation Department",
        "officers": [{"officer_id": "OFC-009", "name": "Dr. Rajan Pillai", "phone": "+91-9900009999", "level": 1, "jurisdiction": "Ward 12", "escalation_label": "Health Officer"}],
    },
    "transport": {
        "dept": "BMTC / Transport Department",
        "officers": [{"officer_id": "OFC-010", "name": "Abdul Kareem", "phone": "+91-9900010000", "level": 1, "jurisdiction": "Zone 1", "escalation_label": "Traffic Inspector"}],
    },
    "police": {
        "dept": "Police Department",
        "officers": [{"officer_id": "OFC-011", "name": "Inspector Prakash", "phone": "+91-9900011111", "level": 1, "jurisdiction": "PS Ward 12", "escalation_label": "Sub-Inspector"}],
    },
    "revenue": {
        "dept": "Revenue Department",
        "officers": [{"officer_id": "OFC-012", "name": "Latha Krishnan", "phone": "+91-9900012222", "level": 1, "jurisdiction": "Taluk Office", "escalation_label": "Revenue Inspector"}],
    },
}
FALLBACK_OFFICERS = [{"officer_id": "OFC-000", "name": "General Officer", "phone": "+91-9900000000", "level": 1, "jurisdiction": "General", "escalation_label": "Ward Officer"}]

def classify_complaint(text: str, image_b64: str | None = None, image_category: str | None = None) -> dict[str, Any]:
    """Classify complaint using Claude AI if available, else comprehensive keyword rules."""
    # ── Try Claude API classification first ────────────────────────────────────
    if OPENAI_AVAILABLE and openai_client:
        try:
            return _classify_with_claude(text, image_b64)
        except Exception as e:
            print(f"[Classify] Claude API failed, falling back to rules: {e}")
    return _classify_with_rules(text)


def _classify_with_claude(text: str, image_b64: str | None) -> dict[str, Any]:
    """Use Claude/OpenAI to classify the complaint into exactly one department."""
    system_prompt = """You are an Indian civic grievance classification system. 
Classify the complaint into EXACTLY ONE category and department. Never assign multiple departments.

CATEGORIES AND DEPARTMENTS (pick exactly one):
- Roads → "Roads & Infrastructure Department" (potholes, road damage, footpath, speed breaker, divider, flyover, bridge, street repair)
- Sewage → "Sewage & Sanitation Department" (sewage overflow, blocked drain, sewer, septic, open defecation, manhole, stormwater drain)
- WaterSupply → "Water Supply Department" (water shortage, tap water, pipeline leak, water pressure, drinking water, borewell, water board)
- Electricity → "Electricity Department" (power cut, street light, electric pole, transformer, wiring, BESCOM, MSEB, short circuit)
- SolidWaste → "Solid Waste Management Department" (garbage, trash, waste, BBMP, litter, dumping, compost, bin)
- Parks → "Parks & Horticulture Department" (park, garden, tree, green space, playground, overgrown, tree fallen)
- BuildingPermit → "Town Planning & Building Department" (illegal construction, encroachment, building permit, layout, property)
- HealthSanitation → "Health & Sanitation Department" (mosquito, stagnant water, dengue, malaria, fumigation, dead animal, food adulteration)
- Transport → "BMTC / Transport Department" (bus, auto, traffic signal, road marking, parking, transport)
- Police → "Police Department" (crime, theft, harassment, noise, law and order, illegal activity)
- Revenue → "Revenue Department" (land record, property tax, mutation, encroachment on government land)
- Other → "General Administration" (anything that doesn't fit above)

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "category": "<CategoryKey>",
  "department": "<Full Department Name>",
  "severity": "<LOW|MEDIUM|HIGH|CRITICAL>",
  "summary": "<one sentence summary in English, max 120 chars>",
  "suggested_action": "<specific action for the officer>",
  "tags": ["<tag1>", "<tag2>"],
  "is_fake": false,
  "fake_reason": "",
  "confidence": 0.95
}"""

    messages = [{"role": "user", "content": f"Classify this civic complaint:\n\n{text}"}]
    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": system_prompt}] + messages,
        temperature=0.1,
        max_tokens=300,
    )
    raw = resp.choices[0].message.content.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = json.loads(raw.strip())
    # Validate required fields
    for key in ("category", "department", "severity", "summary"):
        if key not in result:
            raise ValueError(f"Missing field: {key}")
    return result


# ── Comprehensive keyword-based fallback classifier ────────────────────────────
_CLASSIFICATION_RULES = [
    # (category, department, keywords_en, keywords_kn_hi, severity)
    ("Roads", "Roads & Infrastructure Department",
     ["pothole", "road damage", "footpath", "speed breaker", "divider", "flyover", "bridge crack",
      "road repair", "tar", "asphalt", "pavement", "broken road", "road cave", "road sinking"],
     ["gunta", "raste", "road", "saddak", "pothol", "khadda"],
     "MEDIUM"),
    ("Sewage", "Sewage & Sanitation Department",
     ["sewage", "sewer", "drain overflow", "blocked drain", "septic", "manhole", "drainage", "nala",
      "drain block", "open drain", "foul smell drain", "wastewater", "storm drain"],
     ["chowk", "naali", "manhole", "sewerj", "drain"],
     "HIGH"),
    ("WaterSupply", "Water Supply Department",
     ["water shortage", "no water", "water supply", "tap water", "pipeline leak", "water pipe",
      "drinking water", "water board", "borewell", "water pressure", "water not coming",
      "water contamination", "dirty water", "water tank"],
     ["neeru", "paani", "pani", "water", "tap", "pipeline"],
     "HIGH"),
    ("Electricity", "Electricity Department",
     ["power cut", "no electricity", "street light", "electric pole", "transformer", "wiring",
      "bescom", "mseb", "short circuit", "live wire", "power failure", "load shedding",
      "streetlight", "light not working", "electric spark"],
     ["current", "bijli", "light", "batti", "transformer", "pole"],
     "HIGH"),
    ("SolidWaste", "Solid Waste Management Department",
     ["garbage", "trash", "waste", "bbmp", "litter", "dumping", "rubbish", "solid waste",
      "garbage bin", "waste not collected", "sanitation worker", "sweeper", "garbage pile"],
     ["kudike", "kachara", "kacha", "waste", "garbage"],
     "MEDIUM"),
    ("Parks", "Parks & Horticulture Department",
     ["park", "garden", "tree", "green space", "playground", "overgrown", "tree fallen",
      "fallen tree", "tree branch", "uprooted", "weeds", "grass"],
     ["mara", "park", "garden", "tree", "ped"],
     "LOW"),
    ("BuildingPermit", "Town Planning & Building Department",
     ["illegal construction", "encroachment", "building permit", "layout", "property dispute",
      "unauthorised building", "illegal structure", "road encroachment", "footpath encroachment"],
     ["construction", "building", "ghar", "house", "plot"],
     "MEDIUM"),
    ("HealthSanitation", "Health & Sanitation Department",
     ["mosquito", "stagnant water", "dengue", "malaria", "fumigation", "dead animal",
      "food adulteration", "pest", "rats", "cockroach", "health hazard", "contamination"],
     ["mosquito", "dengue", "malaria", "dead", "pest"],
     "HIGH"),
    ("Transport", "BMTC / Transport Department",
     ["bus", "auto", "traffic signal", "road marking", "parking", "transport", "signal",
      "no bus", "bus stop", "traffic jam", "signal not working", "zebra crossing"],
     ["bus", "signal", "traffic", "gadi"],
     "LOW"),
    ("Police", "Police Department",
     ["crime", "theft", "harassment", "noise", "law and order", "illegal", "eve teasing",
      "drunk driving", "assault", "robbery", "vandalism"],
     ["police", "crime", "chor", "theft"],
     "HIGH"),
    ("Revenue", "Revenue Department",
     ["land record", "property tax", "mutation", "patta", "khata", "encroachment on government"],
     ["khata", "patta", "land", "property tax"],
     "LOW"),
]

def _classify_with_rules(text: str) -> dict[str, Any]:
    """Comprehensive keyword-based classifier. Returns exactly one department."""
    text_lower = (text or "").lower().strip()
    if not text_lower:
        return _default_classification(text)

    best_match = None
    best_score = 0

    for (category, department, keywords_en, keywords_other, severity) in _CLASSIFICATION_RULES:
        score = 0
        for kw in keywords_en:
            if kw in text_lower:
                score += 2 if len(kw) > 6 else 1   # longer keywords = higher confidence
        for kw in keywords_other:
            if kw in text_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_match = (category, department, severity, score)

    if best_match and best_score > 0:
        category, department, severity, score = best_match
        confidence = min(0.95, 0.55 + score * 0.08)
        action_map = {
            "Roads": "Inspect and schedule road repair within 48 hours.",
            "Sewage": "Dispatch sanitation team to clear blockage immediately.",
            "WaterSupply": "Check pipeline and restore water supply.",
            "Electricity": "Dispatch lineman to inspect and fix the electrical issue.",
            "SolidWaste": "Schedule garbage pickup and clean the area.",
            "Parks": "Send horticulture team to address the park issue.",
            "BuildingPermit": "Inspect site for unauthorized construction.",
            "HealthSanitation": "Dispatch health team for inspection and fumigation.",
            "Transport": "Inspect and repair traffic infrastructure.",
            "Police": "Forward complaint to the nearest police station.",
            "Revenue": "Forward to Revenue Officer for verification.",
        }
        tag_map = {
            "Roads": ["infrastructure", severity.lower()],
            "Sewage": ["sanitation", severity.lower()],
            "WaterSupply": ["water", severity.lower()],
            "Electricity": ["power", severity.lower()],
            "SolidWaste": ["waste", severity.lower()],
            "Parks": ["parks", severity.lower()],
            "BuildingPermit": ["planning", severity.lower()],
            "HealthSanitation": ["health", severity.lower()],
            "Transport": ["transport", severity.lower()],
            "Police": ["law-order", severity.lower()],
            "Revenue": ["revenue", severity.lower()],
        }
        return {
            "category": category,
            "department": department,
            "severity": severity,
            "summary": text[:120],
            "suggested_action": action_map.get(category, "Review and take appropriate action."),
            "tags": tag_map.get(category, ["general", severity.lower()]),
            "is_fake": False,
            "fake_reason": "",
            "confidence": round(confidence, 2),
        }

    return _default_classification(text)


def _default_classification(text: str) -> dict[str, Any]:
    return {
        "category": "Other",
        "department": "General Administration",
        "severity": "LOW",
        "summary": (text or "")[:120],
        "suggested_action": "Review complaint and forward to appropriate department.",
        "tags": ["general", "low"],
        "is_fake": False,
        "fake_reason": "",
        "confidence": 0.4,
    }

def get_officers_for_dept(dept_key: str | None, level: int = 1):
    officers = DEPARTMENT_MAP.get(dept_key or "", {}).get("officers", FALLBACK_OFFICERS)
    level_matches = [o for o in officers if o["level"] == level]
    return level_matches if level_matches else FALLBACK_OFFICERS

def dept_key_from_category(category: str) -> str | None:
    return {
        "Roads": "roads",
        "Sewage": "sewage",
        "WaterSupply": "water_supply",
        "Electricity": "electricity",
        "SolidWaste": "solid_waste",
        "Parks": "parks",
        "BuildingPermit": "building_permit",
        "HealthSanitation": "health_sanitation",
        "Transport": "transport",
        "Police": "police",
        "Revenue": "revenue",
        "Other": None,
    }.get(category)

def validate_image_authenticity(image_b64: str | None, complaint_text: str | None = None) -> dict[str, Any]:
    return {"authentic": True, "reason": "Validation skipped", "image_category": None, "matches_text": True}

# ── Database helpers ───────────────────────────────────────────────────────────

def db_insert_complaint(data: dict[str, Any]):
    if not SUPABASE_AVAILABLE:
        LOCAL_DB["complaints"][data["complaint_id"]] = dict(data)
        return {"id": data.get("complaint_id")}
    return supabase.table("complaints").insert(data).execute()

def db_get_complaint(complaint_id: str, secret_hash: str, legacy_hash: str | None = None):
    if not SUPABASE_AVAILABLE:
        complaint = LOCAL_DB["complaints"].get(complaint_id)
        if complaint and complaint.get("secret_hash") in [secret_hash, legacy_hash]:
            return complaint
        return None
    resp = supabase.table("complaints").select("*").eq("complaint_id", complaint_id).eq("secret_hash", secret_hash).limit(1).execute()
    return resp.data[0] if resp.data else None

def db_get_complaint_officer(complaint_id: str):
    if not SUPABASE_AVAILABLE:
        return LOCAL_DB["complaints"].get(complaint_id)
    resp = supabase.table("complaints").select("*").eq("complaint_id", complaint_id).single().execute()
    return resp.data

def db_update_complaint(complaint_id: str, updates: dict[str, Any]):
    if not SUPABASE_AVAILABLE:
        complaint = LOCAL_DB["complaints"].get(complaint_id)
        if complaint:
            complaint.update(updates)
            complaint["updated_at"] = datetime.now(timezone.utc).isoformat()
        return complaint
    return supabase.table("complaints").update(
        {**updates, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("complaint_id", complaint_id).execute()

def db_get_timeline(complaint_id: str):
    if not SUPABASE_AVAILABLE:
        return sorted(LOCAL_DB["timeline"].get(complaint_id, []), key=lambda x: x.get("timestamp", ""))
    resp = supabase.table("complaint_timeline").select("*").eq("complaint_id", complaint_id).order("timestamp", desc=False).execute()
    return resp.data or []

def db_add_timeline(complaint_id: str, actor: str, action: str, details: dict[str, Any] | None = None):
    entry = {
        "complaint_id": complaint_id, "actor": actor, "action": action,
        "details": details or {}, "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if not SUPABASE_AVAILABLE:
        LOCAL_DB["timeline"].setdefault(complaint_id, []).append(entry)
    else:
        supabase.table("complaint_timeline").insert(entry).execute()
    return entry

def db_assign_officer(complaint_id: str, officer: dict[str, Any], hours: int = 48):
    assignment = {
        "complaint_id": complaint_id,
        "officer_id": officer["officer_id"],
        "officer_name": officer["name"],
        "officer_phone": officer["phone"],
        "deadline": _deadline(hours),
        "level": officer.get("level", 1),
        "assigned_at": datetime.now(timezone.utc).isoformat(),
    }
    if not SUPABASE_AVAILABLE:
        LOCAL_DB["assignments"][complaint_id] = assignment
    else:
        supabase.table("officer_assignments").upsert(assignment).execute()
    return assignment

def db_get_assignment(complaint_id: str):
    if not SUPABASE_AVAILABLE:
        return LOCAL_DB["assignments"].get(complaint_id)
    resp = supabase.table("officer_assignments").select("*").eq("complaint_id", complaint_id).order("assigned_at", desc=True).limit(1).execute()
    return resp.data[0] if resp.data else None

def db_add_resolution(complaint_id: str, officer_id: str, evidence_url: str, notes: str):
    entry = {
        "complaint_id": complaint_id, "officer_id": officer_id,
        "evidence_url": evidence_url, "notes": notes,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    if not SUPABASE_AVAILABLE:
        LOCAL_DB["resolution"][complaint_id] = entry
    else:
        supabase.table("resolution_evidence").upsert(entry).execute()
    return entry

def db_get_resolution(complaint_id: str):
    if not SUPABASE_AVAILABLE:
        return LOCAL_DB["resolution"].get(complaint_id)
    resp = supabase.table("resolution_evidence").select("*").eq("complaint_id", complaint_id).limit(1).execute()
    return resp.data[0] if resp.data else None

def db_add_feedback(complaint_id: str, vote: str, latitude: Any = None, longitude: Any = None):
    entry = {
        "complaint_id": complaint_id, "vote": vote,
        "latitude": _coerce_float(latitude), "longitude": _coerce_float(longitude),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if not SUPABASE_AVAILABLE:
        LOCAL_DB["feedback"].setdefault(complaint_id, []).append(entry)
    else:
        supabase.table("community_feedback").insert(entry).execute()
    return entry

def db_get_feedback(complaint_id: str):
    if not SUPABASE_AVAILABLE:
        return LOCAL_DB["feedback"].get(complaint_id, [])
    resp = supabase.table("community_feedback").select("*").eq("complaint_id", complaint_id).execute()
    return resp.data or []

def upload_image(bucket: str, path: str, image_bytes: bytes, content_type: str = "image/jpeg") -> str:
    if not SUPABASE_AVAILABLE:
        return ""
    try:
        supabase.storage.from_(bucket).upload(path, image_bytes, {"content-type": content_type, "upsert": "true"})
        return supabase.storage.from_(bucket).get_public_url(path)
    except Exception as e:
        print(f"[Storage] Upload failed: {e}")
        return ""

# ── Flask app ──────────────────────────────────────────────────────────────────

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    CORS(app)

    @app.route("/")
    def index():
        return send_from_directory("templates", "index.html")

    @app.route("/track")
    def track():
        return send_from_directory("templates", "track.html")

    @app.route("/officer")
    def officer():
        return send_from_directory("templates", "officer.html")

    # ── Submit complaint ────────────────────────────────────────────────────────
    @app.route("/api/complaints/submit", methods=["POST"])
    def submit_complaint():
        text = request.form.get("text", "").strip()
        latitude  = request.form.get("latitude")
        longitude = request.form.get("longitude")
        image     = request.files.get("image")

        if not text and not image:
            return jsonify({"error": "Provide complaint text or an image"}), 400

        complaint_id = _generate_complaint_id()
        image_url, image_b64, image_category = "", None, None

        if image:
            img_bytes  = image.read()
            image_b64  = base64.b64encode(img_bytes).decode("utf-8")
            image_url  = upload_image(
                "complaint-evidence",
                f"{complaint_id}/{image.filename or 'evidence.jpg'}",
                img_bytes,
                image.content_type or "image/jpeg",
            )

        classification = classify_complaint(text, image_b64, image_category)
        if classification.get("is_fake", False):
            return jsonify({"error": "Complaint rejected: appears to be spam", "is_fake": True}), 400

        secret = _generate_secret()
        complaint_data = {
            "complaint_id":   complaint_id,
            "secret_hash":    _hash(_normalize_secret(secret)),
            "text":           text,
            "category":       classification.get("category", "Other"),
            "severity":       classification.get("severity", "MEDIUM"),
            "department":     classification.get("department", "General Administration"),
            "summary":        classification.get("summary", text[:120]),
            "suggested_action": classification.get("suggested_action", ""),
            "tags":           classification.get("tags", []),
            "latitude":       _coerce_float(latitude),
            "longitude":      _coerce_float(longitude),
            "image_url":      image_url,
            "status":         "OPEN",
            "escalation_level": 1,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "updated_at":     datetime.now(timezone.utc).isoformat(),
        }

        db_insert_complaint(complaint_data)
        db_add_timeline(complaint_id, "CITIZEN", "SUBMITTED", {"summary": complaint_data["summary"]})

        officer_obj = get_officers_for_dept(dept_key_from_category(complaint_data["category"]), level=1)[0]
        db_assign_officer(complaint_id, officer_obj, hours=48)
        db_update_complaint(complaint_id, {"status": "ASSIGNED"})
        db_add_timeline(complaint_id, "SYSTEM", "ASSIGNED", {"officer_id": officer_obj["officer_id"], "officer_name": officer_obj["name"]})

        return jsonify({
            "complaint_id":    complaint_id,
            "secret_code":     secret,
            "officer_assigned": officer_obj,
            "status":          "ASSIGNED",
            "storage_mode":    "supabase" if SUPABASE_AVAILABLE else "local-memory",
        }), 201

    # ── Transcribe audio (voice input) ─────────────────────────────────────────
    @app.route("/api/transcribe", methods=["POST"])
    def transcribe_audio():
        audio_file = request.files.get("audio")
        if not audio_file:
            return jsonify({"error": "No audio file provided"}), 400

        if not WHISPER_AVAILABLE:
            return jsonify({
                "error": "whisper_unavailable",
                "message": "Server-side transcription not available. Use browser speech recognition."
            }), 503

        try:
            model = get_whisper_model()
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                audio_file.save(tmp.name)
                # language=None lets Whisper auto-detect (supports Kannada, Hindi, English)
                segments, info = model.transcribe(tmp.name, language=None, beam_size=5)
                text = " ".join(seg.text.strip() for seg in segments).strip()
                os.unlink(tmp.name)
            if not text:
                return jsonify({"error": "Could not transcribe audio. Please speak clearly."}), 422
            return jsonify({"text": text, "language": info.language, "language_probability": info.language_probability})
        except Exception as e:
            print(f"[Transcribe] Error: {e}")
            return jsonify({"error": f"Transcription failed: {str(e)}"}), 500

    # ── Track complaint ─────────────────────────────────────────────────────────
    @app.route("/api/complaints/track", methods=["GET"])
    def track_complaint():
        complaint_id = _normalize_complaint_id(request.args.get("complaint_id", ""))
        secret_code  = request.args.get("secret_code", "")
        complaint    = db_get_complaint(complaint_id, _hash(_normalize_secret(secret_code)), _hash(secret_code.strip()))
        if not complaint:
            return jsonify({"error": "Complaint not found or secret code incorrect"}), 404
        feedback = db_get_feedback(complaint_id)
        return jsonify({
            "complaint":  complaint,
            "timeline":   db_get_timeline(complaint_id),
            "assignment": db_get_assignment(complaint_id),
            "evidence":   db_get_resolution(complaint_id),
            "feedback": {
                "total":        len(feedback),
                "resolved":     sum(1 for f in feedback if f.get("vote") == "resolved"),
                "not_resolved": sum(1 for f in feedback if f.get("vote") == "not_resolved"),
            },
        })

    # ── Resolve complaint (officer) ─────────────────────────────────────────────
    @app.route("/api/complaints/resolve", methods=["POST"])
    @require_officer
    def resolve_complaint():
        complaint_id = _normalize_complaint_id(request.form.get("complaint_id", ""))
        officer_id   = request.form.get("officer_id", "").strip()
        notes        = request.form.get("notes", "").strip()
        image        = request.files.get("image")
        if not complaint_id or not officer_id:
            return jsonify({"error": "complaint_id and officer_id are required"}), 400
        if not db_get_complaint_officer(complaint_id):
            return jsonify({"error": "Complaint not found"}), 404

        image_url = ""
        if image:
            img_bytes = image.read()
            image_url = upload_image(
                "resolution-evidence",
                f"{complaint_id}/resolution/{image.filename or 'proof.jpg'}",
                img_bytes,
                image.content_type or "image/jpeg",
            )

        db_add_resolution(complaint_id, officer_id, image_url, notes)
        db_update_complaint(complaint_id, {"status": "PENDING_VERIFICATION"})
        db_add_timeline(complaint_id, f"OFFICER:{officer_id}", "RESOLUTION_SUBMITTED", {"notes": notes, "evidence_url": image_url})
        return jsonify({"message": "Resolution submitted.", "complaint_id": complaint_id, "status": "PENDING_VERIFICATION"})

    # ── Community feedback ──────────────────────────────────────────────────────
    @app.route("/api/complaints/feedback", methods=["POST"])
    def submit_feedback():
        data         = request.get_json(silent=True) or {}
        complaint_id = _normalize_complaint_id(data.get("complaint_id", ""))
        vote         = str(data.get("vote", "")).strip()
        if not db_get_complaint_officer(complaint_id):
            return jsonify({"error": "Complaint not found"}), 404

        db_add_feedback(complaint_id, vote, data.get("latitude"), data.get("longitude"))
        db_add_timeline(complaint_id, "COMMUNITY", "FEEDBACK", {"vote": vote})

        feedback        = db_get_feedback(complaint_id)
        resolved_v      = sum(1 for f in feedback if f.get("vote") == "resolved")
        not_resolved_v  = sum(1 for f in feedback if f.get("vote") == "not_resolved")
        current_status  = db_get_complaint_officer(complaint_id).get("status")

        if resolved_v >= 3 and current_status == "PENDING_VERIFICATION":
            db_update_complaint(complaint_id, {"status": "CLOSED"})
            db_add_timeline(complaint_id, "SYSTEM", "CLOSED", {"reason": "Community confirmed resolution"})
            current_status = "CLOSED"
        elif not_resolved_v >= 3 and current_status == "PENDING_VERIFICATION":
            db_update_complaint(complaint_id, {"status": "REOPENED"})
            db_add_timeline(complaint_id, "SYSTEM", "REOPENED", {"reason": "Community rejected resolution"})
            current_status = "REOPENED"

        return jsonify({"message": "Feedback recorded", "complaint_id": complaint_id, "status": current_status})

    # ── All complaints (officer dashboard via API) ──────────────────────────────
    @app.route("/api/complaints/all", methods=["GET"])
    @require_officer
    def all_complaints():
        if not SUPABASE_AVAILABLE:
            return jsonify(list(LOCAL_DB["complaints"].values()))
        resp = supabase.table("complaints").select("*").order("timestamp", desc=True).execute()
        return jsonify(resp.data or [])

    # ── Health ──────────────────────────────────────────────────────────────────
    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"ok": True, "supabase": SUPABASE_AVAILABLE})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
