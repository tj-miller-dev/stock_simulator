from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/hello")
def hello():
    return "hello"


@app.get("/world")
def world():
    return "world"


@app.get("/random")
def random():
    import random
    return random.randint(1, 100)
