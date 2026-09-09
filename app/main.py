from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html lang="nl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Clipparty</title>
        <style>
            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }

            body {
                font-family: Arial, sans-serif;
                background: #080808;
                color: white;
            }

            nav {
                padding: 20px 40px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid #222;
            }

            .logo {
                font-size: 24px;
                font-weight: 800;
            }

            nav a {
                color: white;
                text-decoration: none;
                margin-left: 25px;
            }

            .hero {
                min-height: calc(100vh - 75px);
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                text-align: center;
                padding: 40px 20px;
            }

            h1 {
                font-size: 64px;
                margin-bottom: 20px;
            }

            p {
                font-size: 20px;
                color: #aaa;
                margin-bottom: 35px;
            }

            .button {
                display: inline-block;
                padding: 16px 30px;
                background: #7c00ff;
                color: white;
                text-decoration: none;
                border-radius: 10px;
                font-weight: bold;
            }

            .button:hover {
                background: #9200ff;
            }

            @media (max-width: 600px) {
                h1 {
                    font-size: 42px;
                }
            }
        </style>
    </head>

    <body>

        <nav>
            <div class="logo">Clipparty</div>

            <div>
                <a href="/">Home</a>
                <a href="/clip">Clip</a>
            </div>
        </nav>

        <section class="hero">
            <h1>Welkom bij Clipparty</h1>
            <p>Ontdek clips. Maak clips. Verdien met clips.</p>
            <a class="button" href="/clip">Begin met clippen</a>
        </section>

    </body>
    </html>
    """


@app.get("/clip", response_class=HTMLResponse)
def clip():
    return """
    <!DOCTYPE html>
    <html lang="nl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Clipparty - Clip</title>

        <style>
            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }

            body {
                font-family: Arial, sans-serif;
                background: #080808;
                color: white;
            }

            nav {
                padding: 20px 40px;
                border-bottom: 1px solid #222;
            }

            nav a {
                color: white;
                text-decoration: none;
            }

            .container {
                max-width: 900px;
                margin: 80px auto;
                padding: 20px;
                text-align: center;
            }

            h1 {
                font-size: 48px;
                margin-bottom: 20px;
            }

            p {
                color: #aaa;
                font-size: 18px;
                margin-bottom: 40px;
            }

            .box {
                border: 1px solid #333;
                border-radius: 15px;
                padding: 60px 30px;
                background: #101010;
            }

            .button {
                display: inline-block;
                padding: 15px 30px;
                background: #7c00ff;
                color: white;
                border-radius: 10px;
                text-decoration: none;
                font-weight: bold;
            }
        </style>
    </head>

    <body>

        <nav>
            <a href="/">← Terug naar home</a>
        </nav>

        <div class="container">
            <h1>Clip</h1>

            <p>
                Hier komt straks jouw clipomgeving.
            </p>

            <div class="box">
                <h2>Clipparty Clip Studio</h2>
                <br>
                <p>
                    De clipfunctie komt hier.
                </p>

                <a class="button" href="/">Terug naar home</a>
            </div>
        </div>

    </body>
    </html>
    """
