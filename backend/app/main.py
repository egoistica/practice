from fastapi import FastAPI


app = FastAPI(title="Lecture Notes API")


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "backend", "status": "ok"}


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}