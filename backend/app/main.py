from fastapi import FastAPI
from app.routers.framework_router import router as framework_router

app = FastAPI(title="S2P Copilot", version="0.1.0")
app.include_router(framework_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "s2p-copilot", "version": "0.1.0"}
