from fastapi import FastAPI

app = FastAPI()


@app.get("/hello")
def hello():
    return "hello"


@app.get("/world/etc")
def world():
    return "world"
