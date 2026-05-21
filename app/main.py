from fastapi import FastAPI

app = FastAPI(
    title="skillforge",
    version="0.1.0",
    description="Backend of the skill-platform",
)


@app.get("/health")
async def healt():
    return {"status": "ok"}
