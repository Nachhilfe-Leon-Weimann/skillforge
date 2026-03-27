from fastapi import FastAPI

app = FastAPI(
    title="skillforge",
)


@app.get("/health")
async def healt():
    return {"status": "ok"}
