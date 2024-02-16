from flask import Flask,url_for,render_template,request
from dotenv import load_dotenv
import os
import pymongo
import openai

## loading the env variables
load_dotenv()

### setting up open ai and mongodb
openai.api_key = os.getenv("OPEN_AI")
client = pymongo.MongoClient(os.getenv("Mongo_uri"))
db = client.saibabasayings
collection = db.text

## embedding generator
def model(text):
    return openai.embeddings.create(input = [text], model="text-embedding-3-small").data[0].embedding

app = Flask(__name__)



@app.route('/',methods=['POST','GET'])
def index():
    if request.method == 'POST':
        ### generate the embedding and return it to the page
        pass
    else:
        ### else jsutrender the index page
        return render_template("index.html")



if __name__ == "__main__":
    app.run(debug=True)