"""HTTP API по разделам. Каждый модуль — свой APIRouter, подключаются в main.py."""
from . import auth, equipment, inspections, system, vision

ROUTERS = [system.router, auth.router, equipment.router, vision.router, inspections.router]
