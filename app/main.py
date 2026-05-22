from fastapi import FastAPI

from app.api.v1.router import router as v1_router

app = FastAPI(
    title="skillforge",
    version="0.1.0",
    description="Backend of the skill-platform",
)
app.include_router(v1_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
