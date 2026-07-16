from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api import (
    analytics,
    auth,
    networks,
    sections,
    sample_units,
    detection_result,
    pci,
    ws,
)
from app.core.config import settings

# from app.services.pci.pci_calculator import PCICalculator

app = FastAPI(title="Pavement Management API", version="1.0.0")

# PCI_CAlCULATOR = PCICalculator.get_instance()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(ws.router)
app.include_router(auth.router)
app.include_router(analytics.router)
app.include_router(networks.router)
app.include_router(sections.router)
app.include_router(sample_units.router)
app.include_router(detection_result.router)
app.include_router(pci.router)

# Serve uploaded images
# os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
# app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


@app.get("/")
async def root():
    return {"message": "Pavement Management API is running"}
