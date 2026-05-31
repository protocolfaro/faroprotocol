"""FARO PROTOCOL -- Servidor webhook Flask"""
import hashlib, hmac, json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError: pass
from flask import Flask, request, jsonify
_ENGINE_DIR = Path(__file__).parent / "engine"
if _ENGINE_DIR.exists() and str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))